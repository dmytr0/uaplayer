// Портативний запускач UAFix Player для Windows: один .exe, усередині якого — zip зі збіркою
// Flet. Перший запуск версії розпаковує її в %LOCALAPPDATA%\UAFix Player\app-<версія>,
// далі одразу запускає вже розпаковане. Старі версії прибирає.
//
// Шлях до самого запускача передається застосунку в UAFIX_LAUNCHER — за ним оновлення
// в застосунку кладе новий .exe на місце старого.
//
// Оновлення (як в Orange Audio Lab): застосунок перейменовує цей .exe на .old, кладе новий
// на його місце й запускає; новий розпаковує свою версію, відкриває її і прибирає .old.
// Прапорці --wait <pid> / --replace <шлях> лишились для сумісності.
package main

import (
	"archive/zip"
	"bytes"
	_ "embed"
	"io"
	"os"
	"os/exec"
	"path/filepath"
	"strconv"
	"strings"
	"time"
)

//go:embed app.zip
var appZip []byte

var version = "dev" // -ldflags "-X main.version=..."

func extract(dst string) error {
	r, err := zip.NewReader(bytes.NewReader(appZip), int64(len(appZip)))
	if err != nil {
		return err
	}
	for _, f := range r.File {
		name := filepath.FromSlash(strings.ReplaceAll(f.Name, "\\", "/"))
		p := filepath.Join(dst, name)
		if !strings.HasPrefix(p, filepath.Clean(dst)+string(os.PathSeparator)) {
			continue // захист від «../» в архіві
		}
		if f.FileInfo().IsDir() {
			os.MkdirAll(p, 0o755)
			continue
		}
		if err := os.MkdirAll(filepath.Dir(p), 0o755); err != nil {
			return err
		}
		src, err := f.Open()
		if err != nil {
			return err
		}
		out, err := os.Create(p)
		if err != nil {
			src.Close()
			return err
		}
		_, err = io.Copy(out, src)
		src.Close()
		out.Close()
		if err != nil {
			return err
		}
	}
	return nil
}

// waitExit чекає, поки процес pid завершиться (але не довше 60 с).
func waitExit(pid int) {
	p, err := os.FindProcess(pid)
	if err != nil {
		return // уже немає
	}
	done := make(chan struct{})
	go func() {
		p.Wait()
		close(done)
	}()
	select {
	case <-done:
	case <-time.After(60 * time.Second):
	}
	time.Sleep(500 * time.Millisecond)
}

// copyFile копіює src у dst (кілька спроб: старий .exe ще може бути зайнятий системою).
func copyFile(src, dst string) error {
	var err error
	for i := 0; i < 20; i++ {
		if err = copyOnce(src, dst); err == nil {
			return nil
		}
		time.Sleep(500 * time.Millisecond)
	}
	return err
}

func copyOnce(src, dst string) error {
	in, err := os.Open(src)
	if err != nil {
		return err
	}
	defer in.Close()
	tmp := dst + ".new"
	out, err := os.Create(tmp)
	if err != nil {
		return err
	}
	if _, err = io.Copy(out, in); err != nil {
		out.Close()
		os.Remove(tmp)
		return err
	}
	out.Close()
	old := dst + ".old"
	os.Remove(old)
	if err = os.Rename(dst, old); err != nil && !os.IsNotExist(err) {
		os.Remove(tmp)
		return err
	}
	if err = os.Rename(tmp, dst); err != nil {
		os.Rename(old, dst)
		return err
	}
	os.Remove(old) // якщо зайнятий — лишиться, приберемо наступного разу
	return nil
}

func main() {
	self, _ := os.Executable()
	var args []string
	for i := 1; i < len(os.Args); i++ {
		switch {
		case os.Args[i] == "--wait" && i+1 < len(os.Args):
			if pid, err := strconv.Atoi(os.Args[i+1]); err == nil {
				waitExit(pid)
			}
			i++
		case os.Args[i] == "--replace" && i+1 < len(os.Args):
			target := os.Args[i+1]
			if copyFile(self, target) == nil {
				self = target // далі застосунок оновлюватиме саме його
				refreshIcons()
			}
			i++
		default:
			args = append(args, os.Args[i])
		}
	}
	// щойно оновились (поруч лишився попередній .exe) — прибрати його й оновити іконки
	if err := os.Remove(self + ".old"); err == nil {
		refreshIcons()
	}

	base := filepath.Join(os.Getenv("LOCALAPPDATA"), "UAFix Player")
	dir := filepath.Join(base, "app-"+version)
	done := filepath.Join(dir, ".complete")
	if _, err := os.Stat(done); err != nil {
		os.RemoveAll(dir)
		if err := extract(dir); err != nil {
			os.RemoveAll(dir)
			return
		}
		os.WriteFile(done, []byte(version), 0o644)
	}
	cmd := exec.Command(filepath.Join(dir, "uafix-player.exe"), args...)
	cmd.Dir = dir
	cmd.Env = append(os.Environ(), "UAFIX_LAUNCHER="+self)
	if err := cmd.Start(); err != nil {
		return
	}
	// старі версії (якщо котрась ще відкрита — видалиться наступного разу)
	if entries, err := os.ReadDir(base); err == nil {
		for _, e := range entries {
			if e.IsDir() && strings.HasPrefix(e.Name(), "app-") && e.Name() != "app-"+version {
				os.RemoveAll(filepath.Join(base, e.Name()))
			}
		}
	}
}
