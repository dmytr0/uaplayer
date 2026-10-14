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
	"fmt"
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

// logf дописує рядок у %LOCALAPPDATA%\UAFix Player\launcher.log (діагностика оновлень).
func logf(base, format string, args ...any) {
	os.MkdirAll(base, 0o755)
	f, err := os.OpenFile(filepath.Join(base, "launcher.log"),
		os.O_CREATE|os.O_APPEND|os.O_WRONLY, 0o644)
	if err != nil {
		return
	}
	defer f.Close()
	fmt.Fprintf(f, "%s [launcher %s] %s\n", time.Now().Format("2006-01-02 15:04:05"), version,
		fmt.Sprintf(format, args...))
}

// cleanEnv — середовище без змінних, які ставить усередині себе запущений застосунок
// (вбудований Python і Flet: PYTHONHOME, PYTHONPATH, FLET_…, сертифікати). Коли попередня
// версія запускає нову (оновлення), нова інакше успадковувала шляхи до файлів СТАРОЇ версії
// (а їх одразу прибираємо) — і не стартувала. Вручну з Провідника все працювало.
func cleanEnv() []string {
	drop := []string{"PYTHON", "FLET_", "SERIOUS_", "UAFIX_", "SSL_CERT", "REQUESTS_CA",
		"TCL_LIBRARY", "TK_LIBRARY"}
	var env []string
	for _, kv := range os.Environ() {
		up := strings.ToUpper(kv)
		keep := true
		for _, p := range drop {
			if strings.HasPrefix(up, p) {
				keep = false
				break
			}
		}
		if keep {
			env = append(env, kv)
		}
	}
	return env
}

func main() {
	self, _ := os.Executable()
	base := filepath.Join(os.Getenv("LOCALAPPDATA"), "UAFix Player")
	logf(base, "start: %s %q", self, os.Args[1:])
	var args []string
	for i := 1; i < len(os.Args); i++ {
		switch {
		case os.Args[i] == "--wait" && i+1 < len(os.Args):
			if pid, err := strconv.Atoi(os.Args[i+1]); err == nil {
				waitExit(pid)
				logf(base, "previous version (pid %d) exited", pid)
			}
			i++
		case os.Args[i] == "--replace" && i+1 < len(os.Args):
			target := os.Args[i+1]
			if err := copyFile(self, target); err == nil {
				self = target // далі застосунок оновлюватиме саме його
				refreshIcons()
				logf(base, "replaced %s", target)
			} else {
				logf(base, "replace %s failed: %v", target, err)
			}
			i++
		default:
			args = append(args, os.Args[i])
		}
	}
	// щойно оновились (поруч лишився попередній .exe) — прибрати його й оновити іконки
	if err := os.Remove(self + ".old"); err == nil {
		refreshIcons()
		logf(base, "removed previous .exe, icons refreshed")
	}

	dir := filepath.Join(base, "app-"+version)
	done := filepath.Join(dir, ".complete")
	if _, err := os.Stat(done); err != nil {
		os.RemoveAll(dir)
		if err := extract(dir); err != nil {
			logf(base, "extract failed: %v", err)
			os.RemoveAll(dir)
			return
		}
		os.WriteFile(done, []byte(version), 0o644)
		logf(base, "extracted %s", dir)
	}
	appExe := filepath.Join(dir, "uafix-player.exe")
	env := append(cleanEnv(), "UAFIX_LAUNCHER="+self)
	for attempt := 1; attempt <= 2; attempt++ {
		cmd := exec.Command(appExe, args...)
		cmd.Dir = dir
		cmd.Env = env
		if err := cmd.Start(); err != nil {
			logf(base, "start failed: %v", err)
			return
		}
		logf(base, "started app pid %d (attempt %d)", cmd.Process.Pid, attempt)
		// стежимо кілька секунд: якщо застосунок одразу закрився — ще одна спроба
		exited := make(chan error, 1)
		go func() { exited <- cmd.Wait() }()
		select {
		case err := <-exited:
			logf(base, "app exited right away: %v", err)
			time.Sleep(2 * time.Second)
			continue
		case <-time.After(8 * time.Second):
			logf(base, "app is running")
		}
		break
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
