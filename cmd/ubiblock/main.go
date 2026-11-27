// ubiblock puts a read-only block device over a UBI volume, or takes it away, as mtd-utils' ubiblock
// does: the Echo Dot 5th gen's boot script mounts Fire OS's system_a (SquashFS on UBI) through one to
// adopt the unit's own speaker tuning, and busybox has no tool for it. The kernel creates
// /dev/ubiblockD_N for /dev/ubiD_N.
//
//	ubiblock create /dev/ubi0_9
//	ubiblock remove /dev/ubi0_9
package main

import (
	"fmt"
	"os"
	"syscall"
	"unsafe"
)

// The volume ioctls of include/uapi/mtd/ubi-user.h: UBI_IOCVOLCRBLK is _IOW('O', 7, struct
// ubi_blkcreate_req), a struct of 128 padding bytes, and UBI_IOCVOLRMBLK is _IO('O', 8).
const (
	iocVolCreateBlock = 1<<30 | 128<<16 | 'O'<<8 | 7
	iocVolRemoveBlock = 'O'<<8 | 8
)

func main() {
	if len(os.Args) != 3 || (os.Args[1] != "create" && os.Args[1] != "remove") {
		fmt.Fprintln(os.Stderr, "usage: ubiblock create|remove /dev/ubiD_N")
		os.Exit(2)
	}
	f, err := os.OpenFile(os.Args[2], os.O_RDONLY, 0)
	if err != nil {
		fmt.Fprintln(os.Stderr, "ubiblock:", err)
		os.Exit(1)
	}
	defer f.Close()

	var req [128]byte
	cmd, arg := uintptr(iocVolRemoveBlock), uintptr(0)
	if os.Args[1] == "create" {
		cmd, arg = iocVolCreateBlock, uintptr(unsafe.Pointer(&req))
	}
	_, _, errno := syscall.Syscall(syscall.SYS_IOCTL, f.Fd(), cmd, arg)
	switch {
	case errno == 0:
	case errno == syscall.EEXIST && os.Args[1] == "create":
		// Already there, from a boot that stopped before it took it away: use it.
	default:
		fmt.Fprintf(os.Stderr, "ubiblock: %s %s: %v\n", os.Args[1], os.Args[2], errno)
		os.Exit(1)
	}
}
