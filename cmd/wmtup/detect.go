package main

import (
	"fmt"
	"log"
	"os"
	"strings"
	"syscall"
)

// What Amazon's wmt_loader does, and nothing else of it.
//
// The combo chip's drivers are built into this kernel, but their character devices — stpwmt,
// wmtWifi, stpbt — are not registered at boot. The detection driver registers them when userspace
// tells it which chip it is. On this board there is no external combo chip, so the sequence is:
// ask for the SoC's chip id, set it, and run module init with it.
//
// The requests were read out of wmt_loader itself (Fire OS 6574.1) by disassembling its calls, not
// taken from a header: magic 'w', and the chip id passed by value in the argument despite the
// _IOR/_IOW encoding.
const (
	detectMagic = 'w'

	getSocChipID  = 2<<30 | 4<<16 | detectMagic<<8 | 3 // _IOR('w', 3, int): returns the SoC's id
	setChipID     = 1<<30 | 4<<16 | detectMagic<<8 | 1 // _IOW('w', 1, int): the id, by value
	moduleCleanup = 2<<30 | 4<<16 | detectMagic<<8 | 5 // _IOR('w', 5, int): the id, by value
	moduleInit    = 2<<30 | 4<<16 | detectMagic<<8 | 4 // _IOR('w', 4, int): the id, by value
)

// Where the devices appear once registered, and the numbers read off a running unit.
var nodes = []struct {
	path         string
	major, minor uint32
}{
	{"/dev/stpwmt", 190, 0},
	{"/dev/wmtWifi", 153, 0},
	{"/dev/stpbt", 192, 0},
}

var detectDev *string

// registered reports whether the combo chip's devices exist already.
func registered() bool {
	b, err := os.ReadFile("/proc/devices")
	return err == nil && strings.Contains(string(b), "mtk_stp_wmt")
}

// detect registers the combo chip's character devices.
func detect(dev string) error {
	if _, err := os.Stat(dev); err != nil {
		// The detection device is registered at boot, but nothing makes its node without devtmpfs.
		if err := syscall.Mknod(dev, syscall.S_IFCHR|0o600, mkdev(154, 0)); err != nil {
			return fmt.Errorf("mknod %s: %w", dev, err)
		}
	}
	fd, err := syscall.Open(dev, syscall.O_RDWR, 0)
	if err != nil {
		return fmt.Errorf("open %s: %w", dev, err)
	}
	defer syscall.Close(fd)

	// The SoC path of wmt_loader, and only that. External combo chip detection is NOT asked for: on this
	// board there is no external chip's status GPIO, and the driver reads it anyway — a NULL
	// dereference in gpiod_get_raw_value and a watchdog reset, found the hard way. The loader only
	// takes that branch when something tells it an external chip might exist.
	id, err := ioctlRet(fd, getSocChipID, 0)
	if err != nil {
		return fmt.Errorf("GET_SOC_CHIP_ID: %w", err)
	}
	// Anything outside the MediaTek ids the loader itself accepts is a wrong board, not a chip to
	// initialise.
	if id < 0x6000 || id > 0x9000 {
		return fmt.Errorf("unexpected SoC chip id %#x", id)
	}
	log.Printf("SoC chip id %#x", id)

	if _, err := ioctlRet(fd, setChipID, uintptr(id)); err != nil {
		return fmt.Errorf("SET_CHIP_ID(%#x): %w", id, err)
	}
	// Cleanup before init, and init only if cleanup succeeded: the loader does exactly this, and
	// without the cleanup the common driver init fails.
	if _, err := ioctlRet(fd, moduleCleanup, uintptr(id)); err != nil {
		return fmt.Errorf("MODULE_CLEANUP(%#x): %w", id, err)
	}
	// Module init reports failure on this board ("do common driver init failed, ret:-1") and registers
	// the devices regardless — which is also why Amazon's wmt_loader always exits 255 here. So the
	// return value is only logged; whether mtk_stp_wmt now exists is what decides.
	if _, err := ioctlRet(fd, moduleInit, uintptr(id)); err != nil {
		log.Printf("DO_MODULE_INIT(%#x) reported %v (expected on this board)", id, err)
	}
	if !registered() {
		return fmt.Errorf("module init did not register mtk_stp_wmt")
	}
	log.Printf("combo chip devices registered")
	return nil
}

// makeNodes creates the device nodes for the registered devices; there is no devtmpfs to do it.
func makeNodes() {
	for _, n := range nodes {
		if _, err := os.Stat(n.path); err == nil {
			continue
		}
		if err := syscall.Mknod(n.path, syscall.S_IFCHR|0o600, mkdev(n.major, n.minor)); err != nil {
			log.Printf("mknod %s: %v", n.path, err)
		}
	}
}

func mkdev(major, minor uint32) int {
	return int(major<<8 | minor)
}

// ioctlRet is an ioctl whose result is its return value rather than something written back.
func ioctlRet(fd int, req, arg uintptr) (int, error) {
	r, _, errno := syscall.Syscall(syscall.SYS_IOCTL, uintptr(fd), req, arg)
	if errno != 0 {
		return 0, errno
	}
	return int(r), nil
}
