package main

import (
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"
	"time"

	tea "charm.land/bubbletea/v2"
	"charm.land/lipgloss/v2"
	"github.com/charmbracelet/x/ansi"
)

func fixture() model {
	m := newModel(options{Backend: "python3", Script: "/fixture/backend.py", Python: "python3", Theme: "dark", Action: "install", Scope: "user", NoAnimation: true})
	m.loaded = true
	m.width = 120
	m.height = 36
	m.overview = event{Version: "1.0.0", Layouts: map[string]layout{"user": {Runtime: "/tmp/cefont-fixture/lib/cefont", Command: "/tmp/cefont-fixture/bin/cefont", Status: "not installed"}, "system": {Runtime: "/opt/cefont", Command: "/usr/local/bin/cefont", Status: "not installed", Administrator: true}}}
	m.overview.Methods = map[string]methodInfo{"prebuilt": {Available: true, Version: "1.0.0", Detail: "Install the bundled wheel. Local edits are not included."}, "source": {Available: true, Version: "1.0.0", Detail: "Build this checkout, including local patches."}}
	m.checks = []check{{Label: "Python", Status: "ok", Detail: "3.11+ available"}, {Label: "Destinations", Status: "ok", Detail: "Runtime and command paths are available"}}
	m.success = true
	return m
}
func key(code rune) tea.KeyPressMsg { return tea.KeyPressMsg{Code: code} }
func TestReviewDefaultsToCancel(t *testing.T) {
	m := fixture()
	m.screen = "checks"
	next, _ := m.Update(key(tea.KeyEnter))
	m = next.(model)
	if m.screen != "review" || m.choice != 0 {
		t.Fatal("Review must default to Cancel")
	}
	_, cmd := m.Update(key(tea.KeyEnter))
	if cmd == nil {
		t.Fatal("Cancel must exit")
	}
	if _, ok := cmd().(tea.QuitMsg); !ok {
		t.Fatal("Cancel must not execute a backend")
	}
}
func TestBackFromReviewAndDisabledUpdate(t *testing.T) {
	m := fixture()
	m.screen = "review"
	next, _ := m.Update(key('b'))
	m = next.(model)
	if m.screen != "scope" {
		t.Fatal(m.screen)
	}
	m.screen = "action"
	m.choice = 1
	next, _ = m.Update(key(tea.KeyDown))
	if next.(model).choice != 0 {
		t.Fatal("Update must never be selectable")
	}
}
func TestWorkingQuitWaitsForCleanup(t *testing.T) {
	m := fixture()
	m.screen = "progress"
	m.busy = true
	next, cmd := m.Update(key('q'))
	m = next.(model)
	if !m.stopping || !m.busy || cmd != nil {
		t.Fatal("Quit must wait for backend cleanup")
	}
}
func TestBackendMustReportSuccessfulCompletion(t *testing.T) {
	m := fixture()
	m.screen = "progress"
	m.busy = true
	m.success = true
	m.resultSeen = false
	next, _ := m.Update(finishedMsg{})
	m = next.(model)
	if m.success || m.errorText == "" {
		t.Fatal("EOF is not proof of success")
	}
}
func TestReadOnlyCommandsNeverCarryConfirmation(t *testing.T) {
	for _, mode := range []string{"overview", "check"} {
		if strings.Contains(strings.Join(arguments(fixture().opts, mode), " "), "--yes") {
			t.Fatal(mode)
		}
	}
	if !strings.Contains(strings.Join(arguments(fixture().opts, "execute"), " "), "--yes") {
		t.Fatal("Execution needs explicit confirmation")
	}
}
func TestFramesFitAndKeepActionsVisible(t *testing.T) {
	for _, size := range [][2]int{{60, 20}, {80, 24}, {100, 24}, {100, 28}, {100, 29}, {120, 30}, {120, 31}, {120, 32}, {120, 36}, {140, 30}, {160, 45}} {
		for _, theme := range []string{"light", "dark"} {
			for _, screen := range []string{"welcome", "action", "method", "scope", "checks", "review", "progress", "done"} {
				m := fixture()
				m.width = size[0]
				m.height = size[1]
				m.opts.Theme = theme
				m.screen = screen
				frame := ansi.Strip(m.View().Content)
				if lipgloss.Width(frame) > m.width || lipgloss.Height(frame) > m.height {
					t.Fatalf("%s %s %v exceeds viewport: %dx%d", screen, theme, size, lipgloss.Width(frame), lipgloss.Height(frame))
				}
				if screen == "review" && !strings.Contains(frame, "Apply changes") {
					t.Fatalf("Review buttons clipped at %v", size)
				}
				if screen == "welcome" && !strings.Contains(frame, "Begin setup") {
					t.Fatalf("Welcome action clipped at %v", size)
				}
				if folder := os.Getenv("CEFONT_UI_SNAPSHOTS"); folder != "" && size == [2]int{120, 36} {
					if err := os.WriteFile(filepath.Join(folder, theme+"-"+screen+".ansi"), []byte(m.View().Content), 0600); err != nil {
						t.Fatal(err)
					}
				}
			}
		}
	}
}
func TestResizeGuardCannotStartOperation(t *testing.T) {
	m := fixture()
	m.width = 40
	m.height = 12
	m.screen = "review"
	m.choice = 2
	_, cmd := m.Update(key(tea.KeyEnter))
	if cmd != nil {
		t.Fatal("No mutation while layout cannot show review")
	}
}
func TestWorkerStopsBeforeCommandStarts(t *testing.T) {
	python, err := exec.LookPath("python3")
	if err != nil {
		t.Fatal(err)
	}
	dir := t.TempDir()
	script := filepath.Join(dir, "backend.py")
	marker := filepath.Join(dir, "unexpected")
	if err := os.WriteFile(script, []byte(fmt.Sprintf("open(%q,'w').write('unexpected')", marker)), 0600); err != nil {
		t.Fatal(err)
	}
	w := &worker{}
	cmd := w.start(options{Backend: python, Script: script, Python: python}, "check")
	w.stop()
	msg := cmd()
	if _, ok := msg.(finishedMsg); !ok {
		t.Fatal(msg)
	}
	w.close()
	if _, err := os.Stat(marker); !os.IsNotExist(err) {
		t.Fatal("Cancelled operation started")
	}
}
func TestWorkerSignalsBackendAndWaitsForRollback(t *testing.T) {
	python, err := exec.LookPath("python3")
	if err != nil {
		t.Fatal(err)
	}
	dir := t.TempDir()
	script := filepath.Join(dir, "backend.py")
	marker := filepath.Join(dir, "owned-temporary")
	code := fmt.Sprintf(`import json,os,signal,time
path=%q
def stop(*args):
 os.unlink(path)
 print(json.dumps({'type':'result','ok':False,'cancelled':True}),flush=True)
 raise SystemExit(130)
signal.signal(signal.SIGTERM,stop)
open(path,'w').write('temporary fixture')
print(json.dumps({'type':'stage','message':'Ready'}),flush=True)
while True: time.sleep(.01)
`, marker)
	if err := os.WriteFile(script, []byte(code), 0600); err != nil {
		t.Fatal(err)
	}
	w := &worker{}
	cmd := w.start(options{Backend: python, Script: script, Python: python}, "execute")
	first := make(chan tea.Msg, 1)
	go func() { first <- cmd() }()
	select {
	case msg := <-first:
		if _, ok := msg.(event); !ok {
			t.Fatal(msg)
		}
	case <-time.After(5 * time.Second):
		t.Fatal("Backend did not start")
	}
	w.close()
	if _, err := os.Stat(marker); !os.IsNotExist(err) {
		t.Fatal("Rollback did not finish")
	}
}

func TestMethodPageDefaultsToPrebuilt(t *testing.T) {
	m := fixture()
	m.screen = "action"
	m.choice = 0
	next, _ := m.Update(key(tea.KeyEnter))
	m = next.(model)
	if m.screen != "method" || m.choice != 0 || m.opts.Method != "prebuilt" {
		t.Fatal("Install must offer prebuilt by default")
	}
	next, cmd := m.Update(key(tea.KeyEnter))
	m = next.(model)
	if m.screen != "scope" || m.opts.Method != "prebuilt" || cmd != nil {
		t.Fatal("Selecting a method must not deploy")
	}
}
func TestSourceMethodIsExplicitAndPreserved(t *testing.T) {
	m := fixture()
	m.screen = "method"
	m.choice = 1
	next, _ := m.Update(key(tea.KeyEnter))
	m = next.(model)
	if m.opts.Method != "source" || m.screen != "scope" {
		t.Fatal("Source selection lost")
	}
	next, _ = m.Update(key('b'))
	m = next.(model)
	if m.screen != "method" || m.choice != 1 {
		t.Fatal("Back must preserve the source selection")
	}
	if !strings.Contains(strings.Join(arguments(m.opts, "check"), " "), "--method source") {
		t.Fatal("Method was not sent to backend")
	}
}
func TestUnavailableMethodNeverFallsBack(t *testing.T) {
	m := fixture()
	m.screen = "method"
	m.choice = 1
	m.overview.Methods["source"] = methodInfo{Detail: "Application checkout is not included in this package."}
	next, cmd := m.Update(key(tea.KeyEnter))
	m = next.(model)
	if m.screen != "method" || m.errorText == "" || cmd != nil {
		t.Fatal("Unavailable method must explain failure without running")
	}
}
func TestUninstallSkipsMethodPage(t *testing.T) {
	m := fixture()
	m.screen = "action"
	m.choice = 1
	next, cmd := m.Update(key(tea.KeyEnter))
	m = next.(model)
	if m.screen != "scope" || m.opts.Action != "uninstall" || cmd != nil {
		t.Fatal("Uninstall must not ask about a build method")
	}
}
