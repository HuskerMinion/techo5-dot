// wmtup brings up the Echo Dot's MediaTek combo chip (Wi-Fi and Bluetooth) without Amazon's
// wmt_launcher, and stays as the daemon the driver needs while the chip is powered.
//
// The driver expects two things from userspace. First, the host interface: SET_PATCH_NAME then
// SET_STP_MODE, whose argument is (fm << 4) | stp — BTIF on this board. Second, and the reason
// Amazon ships a launcher at all: powering the chip makes the driver ask userspace where the ROM
// patches are. It posts the string "srh_patch" on the same file descriptor and blocks until someone
// answers with SET_PATCH_NUM, one SET_PATCH_INFO per patch, and "ok". It does not care who answers.
//
// What registers /dev/stpwmt in the first place is Amazon's wmt_loader, which detects the chip over
// /dev/wmtdetect; that one is still run from the Android system partition.
//
// Every number here comes from EchoMuse's emOS (MIT, Wil Bowes), which read them off Amazon's own
// launcher under an ioctl shim rather than guessing — see NOTICE. The costly details are the patch
// download order, which runs backwards through the sorted names, and the patch address, whose two
// live bytes are at header offset 0x1A with the top two zero.
//
//	wmtup -patches /system/vendor/firmware/ [-power] [-timeout 30s]
package main

import (
	"flag"
	"fmt"
	"log"
	"os"
	"sort"
	"strings"
	"syscall"
	"time"
	"unsafe"
)

// The driver's ioctls (wmt_dev.h), encoded the asm-generic way: direction in the top two bits, then
// the argument size, the magic and the number.
const (
	iocWrite = 1
	iocMagic = 0xa0
)

func iow(nr, size uintptr) uintptr {
	return iocWrite<<30 | size<<16 | iocMagic<<8 | nr
}

var (
	setPatchName = iow(4, 4)  // char * — the directory, for the driver's own logging
	setSTPMode   = iow(5, 4)  // int — (fm << 4) | stp
	setPatchNum  = iow(14, 4) // int — how many patches follow
	setPatchInfo = iow(15, 4) // struct wmt_patch_info *
)

// The host interface this board uses: STP over BTIF, FM over the common path (wmt_dev.h, wmt_core.h).
// A value the driver does not recognise is rejected with no hardware touched, so a wrong one fails
// safe.
const (
	stpBTIFFull = 0x3
	fmComm      = 0x2
	hifArg      = fmComm<<4 | stpBTIFFull
)

// patchInfo is the driver's struct wmt_patch_info. Its shape is fixed by the driver's
// copy_from_user, so neither the field order nor the 256-byte name is ours to choose.
type patchInfo struct {
	Seq  uint32
	Addr [4]byte
	Name [256]byte
}

// addrOffset is where the two live address bytes sit in the 28-byte patch header. 0x18 is the tail
// of ucPLat, so reading four bytes from there puts rubbish in the high half; the top two are zero.
const addrOffset = 0x1a

const maxPatches = 8

func main() {
	dir := flag.String("patches", "/system/vendor/firmware/", "directory holding the ROM patches (*_hdr.bin)")
	dev := flag.String("dev", "/dev/stpwmt", "the driver's control device")
	power := flag.Bool("power", false, "power the Wi-Fi side on afterwards (writes 1 to /dev/wmtWifi)")
	wifiDev := flag.String("wifi", "/dev/wmtWifi", "the Wi-Fi power device")
	timeout := flag.Duration("timeout", 40*time.Second, "how long to wait for wlan0 after powering on")
	detectDev = flag.String("detect", "/dev/wmtdetect", "the driver's detection device")
	flag.Parse()

	log.SetFlags(log.Ltime)
	if !strings.HasSuffix(*dir, "/") {
		*dir += "/"
	}

	// Registering the driver's character devices is what Amazon's wmt_loader was for. Done here, it
	// takes nothing from Android at all; skipped when something already did it this boot, because the
	// driver's module init is not meant to run twice.
	if registered() {
		log.Printf("combo chip devices already registered")
	} else if err := detect(*detectDev); err != nil {
		log.Fatalf("chip detection: %v", err)
	}
	makeNodes()

	fd, err := syscall.Open(*dev, syscall.O_RDWR, 0)
	if err != nil {
		log.Fatalf("open %s: %v", *dev, err)
	}

	if err := ioctlPtr(fd, setPatchName, unsafe.Pointer(cstring(*dir))); err != nil {
		// Only the driver's own logging uses it; the paths it opens come from SET_PATCH_INFO.
		log.Printf("SET_PATCH_NAME: %v (continuing)", err)
	}
	if err := ioctlInt(fd, setSTPMode, hifArg); err != nil {
		log.Fatalf("SET_STP_MODE(%#x): %v", hifArg, err)
	}
	log.Printf("host interface configured (hif %#x)", hifArg)

	// The daemon has to outlive the bring-up: the driver blocks its power-on until the patches are
	// answered, and asks again after a chip reset.
	go serve(fd, *dir)

	if !*power {
		log.Printf("answering patch searches on %s; nothing else to do", *dev)
		select {}
	}

	// Powering the chip blocks for about thirteen seconds while the firmware loads.
	log.Printf("powering the Wi-Fi side on")
	if err := os.WriteFile(*wifiDev, []byte("1"), 0); err != nil {
		log.Fatalf("write %s: %v", *wifiDev, err)
	}

	deadline := time.Now().Add(*timeout)
	for time.Now().Before(deadline) {
		if _, err := os.Stat("/sys/class/net/wlan0"); err == nil {
			log.Printf("wlan0 is up after %s", time.Until(deadline).Round(time.Second))
			select {}
		}
		time.Sleep(time.Second)
	}
	log.Printf("no wlan0 after %s; staying up so the driver still has an answerer", *timeout)
	select {}
}

// serve answers the driver for as long as the chip is up.
//
// It waits in poll rather than reading in a loop, and an empty read is nothing rather than a
// request. Both matter: this device reports itself readable whether or not it has anything to say,
// and the read then gives a buffer of zeros. Answering those with "fail" tells the driver its patch
// search failed, thousands of times a second — the first version wrote a 50 MB log in two minutes.
func serve(fd int, dir string) {
	buf := make([]byte, 64)
	for {
		if err := wait(fd); err != nil {
			log.Printf("poll: %v", err)
			return
		}
		n, err := syscall.Read(fd, buf)
		if err != nil {
			if err == syscall.EINTR {
				continue
			}
			log.Printf("read: %v", err)
			return
		}
		cmd := strings.TrimRight(string(buf[:n]), "\x00\n\r ")
		if cmd == "" {
			time.Sleep(200 * time.Millisecond)
			continue
		}
		switch {
		case strings.HasPrefix(cmd, "srh_patch"):
			got := answer(fd, dir)
			log.Printf("srh_patch: %d patch(es)", got)
			reply := "fail"
			if got > 0 {
				// Anything but "ok" is read as failure, so say it only when there was something.
				reply = "ok"
			}
			if _, err := syscall.Write(fd, []byte(reply)); err != nil {
				log.Printf("reply %q: %v", reply, err)
			}
		default:
			log.Printf("unhandled request %q", cmd)
			if _, err := syscall.Write(fd, []byte("fail")); err != nil {
				log.Printf("reply: %v", err)
			}
		}
	}
}

// answer tells the driver which patches to download and where each one goes.
func answer(fd int, dir string) int {
	entries, err := os.ReadDir(dir)
	if err != nil {
		log.Printf("read %s: %v", dir, err)
		return 0
	}
	var names []string
	for _, e := range entries {
		// The ROM patches are the *_hdr.bin files. WIFI_RAM_CODE_* and the .cfg beside them are not
		// patches, and counting them leaves the driver waiting for a download that never comes.
		if strings.HasSuffix(e.Name(), "_hdr.bin") {
			names = append(names, e.Name())
		}
	}
	if len(names) == 0 {
		log.Printf("no *_hdr.bin in %s", dir)
		return 0
	}
	if len(names) > maxPatches {
		names = names[:maxPatches]
	}
	// Sort rather than trust the directory order, which is the filesystem's and not stable.
	sort.Strings(names)

	if err := ioctlInt(fd, setPatchNum, len(names)); err != nil {
		log.Printf("SET_PATCH_NUM(%d): %v", len(names), err)
		return 0
	}
	for i, name := range names {
		full := dir + name
		info := patchInfo{
			// The download order runs backwards through the sorted names: Amazon's launcher gives
			// ROMv2_lm_patch_1_0 sequence 2 and _1_1 sequence 1, so the higher-numbered file goes first.
			Seq: uint32(len(names) - i),
		}
		if addr, err := patchAddr(full); err != nil {
			log.Printf("%s: no header address: %v", name, err)
		} else {
			info.Addr = addr
		}
		// The full path, not a bare name: the driver opens this string exactly as given from kernel
		// context, so a bare name resolves against / and fails.
		copy(info.Name[:], full)

		if err := ioctlPtr(fd, setPatchInfo, unsafe.Pointer(&info)); err != nil {
			log.Printf("SET_PATCH_INFO(%d, %s): %v", info.Seq, name, err)
		}
	}
	return len(names)
}

// patchAddr reads the address the driver splices into its download command.
func patchAddr(path string) ([4]byte, error) {
	var out [4]byte
	f, err := os.Open(path)
	if err != nil {
		return out, err
	}
	defer f.Close()

	hdr := make([]byte, addrOffset+2)
	if _, err := f.Read(hdr); err != nil {
		return out, err
	}
	out[2], out[3] = hdr[addrOffset], hdr[addrOffset+1]
	return out, nil
}

// pollfd is poll(2)'s argument.
type pollfd struct {
	fd      int32
	events  int16
	revents int16
}

const pollIn = 0x1

// wait blocks until the driver has something to say.
func wait(fd int) error {
	fds := []pollfd{{fd: int32(fd), events: pollIn}}
	for {
		_, _, errno := syscall.Syscall(syscall.SYS_POLL, uintptr(unsafe.Pointer(&fds[0])), 1, uintptr(pollTimeoutMS))
		switch errno {
		case 0:
			if fds[0].revents&pollIn != 0 {
				return nil
			}
			// Timed out with nothing to read: go round again, so a chip reset still finds us here.
			fds[0].revents = 0
		case syscall.EINTR:
		default:
			return errno
		}
	}
}

// pollTimeoutMS keeps the wait interruptible without making it a spin.
const pollTimeoutMS = 60_000

func ioctlInt(fd int, req uintptr, v int) error {
	return ioctlRaw(fd, req, uintptr(v))
}

func ioctlPtr(fd int, req uintptr, p unsafe.Pointer) error {
	return ioctlRaw(fd, req, uintptr(p))
}

func ioctlRaw(fd int, req, arg uintptr) error {
	if _, _, errno := syscall.Syscall(syscall.SYS_IOCTL, uintptr(fd), req, arg); errno != 0 {
		return fmt.Errorf("ioctl %#x: %w", req, errno)
	}
	return nil
}

// cstring keeps the bytes alive for the duration of the call through the returned pointer.
func cstring(s string) *byte {
	b := append([]byte(s), 0)
	return &b[0]
}
