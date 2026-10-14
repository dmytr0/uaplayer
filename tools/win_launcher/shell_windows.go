//go:build windows

package main

import "syscall"

// refreshIcons просить Провідник перечитати іконки: після оновлення .exe лежить на тому
// самому місці, і Windows показувала старі (стандартні Flet) зображення з кешу іконок.
func refreshIcons() {
	shell32 := syscall.NewLazyDLL("shell32.dll")
	proc := shell32.NewProc("SHChangeNotify")
	if proc.Find() != nil {
		return
	}
	const shcneAssocChanged = 0x08000000
	const shcnfIDList = 0x0000
	proc.Call(shcneAssocChanged, shcnfIDList, 0, 0)
}
