// Портативний запускач UAFix Player для Windows: один .exe, усередині якого — zip зі збіркою
// Flet. Перший запуск версії розпаковує її в %LOCALAPPDATA%\UAFix Player\app-<версія>,
// далі одразу запускає вже розпаковане. Старі версії прибирає.
//
// Шлях до самого запускача передається застосунку в UAFIX_LAUNCHER — за ним оновлення
// в застосунку кладе новий .exe на місце старого.
package main

import (
	"archive/zip"
	"bytes"
	_ "embed"
	"io"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
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

func main() {
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
	self, _ := os.Executable()
	cmd := exec.Command(filepath.Join(dir, "uafix-player.exe"), os.Args[1:]...)
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
