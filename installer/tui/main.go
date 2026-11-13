// CeFont setup shell. Filesystem mutations are owned by the Python backend.
package main

import (
	"bufio"
	"bytes"
	"encoding/json"
	"errors"
	"flag"
	"fmt"
	"image/color"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"sync"
	"syscall"
	"time"
	"unicode"

	"charm.land/bubbles/v2/spinner"
	"charm.land/bubbles/v2/viewport"
	tea "charm.land/bubbletea/v2"
	"charm.land/lipgloss/v2"
	"github.com/charmbracelet/x/ansi"
)

type options struct {
	Backend, Script, Python, Prefix, Action, Scope, Theme, Method string
	NoAnimation, DryRun                                           bool
}
type layout struct {
	Runtime, Command, Status, Error string
	Administrator                   bool
}
type methodInfo struct {
	Available       bool
	Version, Detail string
}

type check struct{ Label, Status, Detail, Hint string }
type event struct {
	Methods                                  map[string]methodInfo
	Type, Message, Version, Command, Runtime string
	PathStatus                               string `json:"path_status"`
	OK, Cancelled                            bool
	Layouts                                  map[string]layout
	Checks                                   []check
}
type overviewMsg struct {
	Event event
	Err   error
}
type finishedMsg struct{ Err error }
type authMsg struct{ Err error }
type clockMsg time.Time

type worker struct {
	mu                         sync.Mutex
	cmd                        *exec.Cmd
	done                       chan struct{}
	events                     chan tea.Msg
	cancelled, started, closed bool
}

func (w *worker) stop() {
	w.mu.Lock()
	defer w.mu.Unlock()
	if w.cancelled {
		return
	}
	w.cancelled = true
	if w.cmd != nil && w.cmd.Process != nil {
		_ = w.cmd.Process.Signal(syscall.SIGTERM)
	}
}
func (w *worker) close() {
	w.stop()
	w.mu.Lock()
	w.closed = true
	done, events, started := w.done, w.events, w.started
	w.mu.Unlock()
	if done == nil || !started {
		return
	}
	// Drain messages even after the UI exits, while Python completes rollback.
	for {
		select {
		case <-done:
			return
		case <-events:
		}
	}
}
func arguments(o options, mode string) []string {
	method := o.Method
	if method == "" {
		method = "prebuilt"
	}
	a := []string{"-I", "-B", o.Script, "--ui-json", mode, "--plain", "--python", o.Python, "--method", method}
	if o.Prefix != "" {
		a = append(a, "--prefix", o.Prefix)
	}
	if o.Action != "" {
		a = append(a, o.Action)
	}
	if o.Scope != "" {
		a = append(a, "--"+o.Scope)
	}
	if mode == "execute" {
		a = append(a, "--yes")
	}
	if o.DryRun {
		a = append(a, "--dry-run")
	}
	return a
}
func (w *worker) start(o options, mode string) tea.Cmd {
	w.mu.Lock()
	w.events = make(chan tea.Msg, 128)
	w.done = make(chan struct{})
	w.cancelled = false
	w.started = false
	events, done := w.events, w.done
	w.mu.Unlock()
	return func() tea.Msg {
		w.mu.Lock()
		if w.cancelled || w.closed {
			w.mu.Unlock()
			close(done)
			return finishedMsg{errors.New("Operation cancelled before start")}
		}
		w.started = true
		w.mu.Unlock()
		go func() {
			defer close(done)
			cmd := exec.Command(o.Backend, arguments(o, mode)...)
			stdout, err := cmd.StdoutPipe()
			if err != nil {
				events <- finishedMsg{err}
				return
			}
			var stderr bytes.Buffer
			cmd.Stderr = &stderr
			w.mu.Lock()
			if w.cancelled || w.closed {
				w.mu.Unlock()
				events <- finishedMsg{errors.New("Operation cancelled before start")}
				return
			}
			w.cmd = cmd
			err = cmd.Start()
			w.mu.Unlock()
			if err != nil {
				events <- finishedMsg{err}
				return
			}
			scanner := bufio.NewScanner(stdout)
			scanner.Buffer(make([]byte, 4096), 1024*1024)
			var protocolErr error
			for scanner.Scan() {
				var e event
				if err := json.Unmarshal(scanner.Bytes(), &e); err != nil {
					protocolErr = fmt.Errorf("Invalid backend event: %w", err)
					continue
				}
				events <- e
			}
			scanErr := scanner.Err()
			err = cmd.Wait()
			w.mu.Lock()
			w.cmd = nil
			w.mu.Unlock()
			if scanErr != nil {
				err = scanErr
			}
			if protocolErr != nil {
				err = protocolErr
			}
			if err != nil && stderr.Len() > 0 {
				err = fmt.Errorf("%w: %s", err, clean(stderr.String()))
			}
			events <- finishedMsg{err}
		}()
		return <-events
	}
}
func waitEvent(events <-chan tea.Msg) tea.Cmd { return func() tea.Msg { return <-events } }
func loadOverview(o options) tea.Cmd {
	return func() tea.Msg {
		cmd := exec.Command(o.Backend, arguments(o, "overview")...)
		data, err := cmd.CombinedOutput()
		if err != nil {
			return overviewMsg{Err: fmt.Errorf("Cannot read setup status: %s", clean(string(data)))}
		}
		var e event
		if err = json.Unmarshal(bytes.TrimSpace(data), &e); err != nil {
			return overviewMsg{Err: err}
		}
		if e.Type != "overview" {
			return overviewMsg{Err: errors.New("Unexpected setup status response")}
		}
		return overviewMsg{Event: e}
	}
}
func clean(s string) string {
	return strings.Map(func(r rune) rune {
		if r == '\n' || r == '\t' {
			return r
		}
		if unicode.IsControl(r) || unicode.In(r, unicode.Cf) {
			return -1
		}
		return r
	}, ansi.Strip(s))
}

// Keep resets inside nested styles on this surface, rather than exposing the
// user's terminal background. This uses SGR only; no terminal palette is edited.
func paint(s string, bg, fg color.Color) string {
	r, g, b, _ := bg.RGBA()
	fr, fgValue, fb, _ := fg.RGBA()
	base := fmt.Sprintf("\x1b[48;2;%d;%d;%dm\x1b[38;2;%d;%d;%dm", r>>8, g>>8, b>>8, fr>>8, fgValue>>8, fb>>8)
	s = strings.ReplaceAll(s, "\x1b[0m", "\x1b[0m"+base)
	s = strings.ReplaceAll(s, "\x1b[m", "\x1b[m"+base)
	s = strings.ReplaceAll(s, "\n", "\n"+base)
	return base + s + "\x1b[0m"
}

type palette struct{ bg, panel, text, muted, line, accent, ink color.Color }

func colors(light bool) palette {
	if light {
		return palette{lipgloss.Color("#EDF1EA"), lipgloss.Color("#E0E8DC"), lipgloss.Color("#17271E"), lipgloss.Color("#4B6352"), lipgloss.Color("#95AA98"), lipgloss.Color("#275D3B"), lipgloss.Color("#F0F5ED")}
	}
	return palette{lipgloss.Color("#101A16"), lipgloss.Color("#17271F"), lipgloss.Color("#E2EDDE"), lipgloss.Color("#A2B7A5"), lipgloss.Color("#3C5947"), lipgloss.Color("#A6D49D"), lipgloss.Color("#142018")}
}

type model struct {
	opts                                        options
	width, height                               int
	screen                                      string
	choice, scroll                              int
	loaded, busy, stopping, success, resultSeen bool
	overview                                    event
	checks                                      []check
	errorText, current, pathStatus              string
	completed                                   []string
	started                                     time.Time
	spin                                        spinner.Model
	work                                        *worker
}

func newModel(o options) model {
	if o.Method == "" {
		o.Method = "prebuilt"
	}
	light := o.Theme == "light" || (o.Theme == "auto" && (strings.HasSuffix(os.Getenv("COLORFGBG"), ";15") || strings.HasSuffix(os.Getenv("COLORFGBG"), ";7")))
	if light {
		o.Theme = "light"
	}
	sp := spinner.New()
	sp.Spinner = spinner.Dot
	sp.Style = lipgloss.NewStyle().Foreground(colors(light).accent)
	return model{opts: o, screen: "welcome", width: 80, height: 24, spin: sp, work: &worker{}}
}
func (m model) Init() tea.Cmd {
	if m.opts.NoAnimation {
		return loadOverview(m.opts)
	}
	return tea.Batch(loadOverview(m.opts), m.spin.Tick)
}
func clock() tea.Cmd { return tea.Tick(time.Second, func(t time.Time) tea.Msg { return clockMsg(t) }) }
func (m *model) begin(mode string) tea.Cmd {
	m.busy = true
	m.stopping = false
	m.success = false
	m.resultSeen = false
	m.scroll = 0
	m.errorText = ""
	m.started = time.Now()
	m.current = "Checking prerequisites"
	m.completed = nil
	if mode == "check" {
		m.screen = "checks"
		m.checks = nil
	} else {
		m.screen = "progress"
		m.current = "Starting deployment"
	}
	return tea.Batch(m.work.start(m.opts, mode), clock())
}
func (m model) Update(msg tea.Msg) (tea.Model, tea.Cmd) {
	switch msg := msg.(type) {
	case tea.WindowSizeMsg:
		m.width = msg.Width
		m.height = msg.Height
	case overviewMsg:
		if msg.Err != nil {
			m.errorText = msg.Err.Error()
		} else {
			m.overview = msg.Event
			m.loaded = true
		}
	case spinner.TickMsg:
		if !m.opts.NoAnimation {
			var cmd tea.Cmd
			m.spin, cmd = m.spin.Update(msg)
			return m, cmd
		}
	case clockMsg:
		if m.busy {
			return m, clock()
		}
	case event:
		switch msg.Type {
		case "stage":
			m.current = clean(msg.Message)
		case "stage_done":
			m.completed = append(m.completed, clean(msg.Message))
		case "checks":
			m.checks = msg.Checks
		case "complete":
			m.pathStatus = clean(msg.PathStatus)
		case "error":
			m.errorText = clean(msg.Message)
		case "result":
			m.resultSeen = true
			m.success = msg.OK
			if msg.Message != "" {
				m.errorText = clean(msg.Message)
			}
		}
		return m, waitEvent(m.work.events)
	case finishedMsg:
		m.busy = false
		if msg.Err != nil && m.errorText == "" {
			m.errorText = clean(msg.Err.Error())
		}
		if !m.resultSeen && m.errorText == "" {
			m.errorText = "The backend stopped without a completion result."
		}
		m.success = m.success && msg.Err == nil && m.resultSeen
		if m.screen == "progress" {
			m.screen = "done"
		}
		m.choice = 0
	case authMsg:
		if msg.Err != nil {
			m.errorText = "Administrator authentication was cancelled or failed. No deployment was started."
			m.screen = "done"
			m.success = false
			return m, nil
		}
		return m, m.begin("execute")
	case tea.KeyPressMsg:
		key := msg.String()
		if key == "q" || key == "ctrl+c" {
			if m.busy {
				m.stopping = true
				m.current = "Stopping safely; waiting for backend cleanup"
				m.work.stop()
				return m, nil
			}
			return m, tea.Quit
		}
		if m.width < 60 || m.height < 20 {
			return m, nil
		}
		if m.busy {
			if key == "pgdown" {
				m.scroll += 5
			}
			if key == "pgup" {
				m.scroll = max(0, m.scroll-5)
			}
			return m, nil
		}
		if key == "pgdown" || key == "ctrl+d" {
			m.scroll += 5
			return m, nil
		}
		if key == "pgup" || key == "ctrl+u" {
			m.scroll = max(0, m.scroll-5)
			return m, nil
		}
		if key == "esc" || key == "b" || key == "left" {
			m.choice = 0
			m.scroll = 0
			switch m.screen {
			case "action":
				m.screen = "welcome"
			case "method":
				m.screen = "action"
			case "scope":
				if m.opts.Action == "install" {
					m.screen = "method"
					if m.opts.Method == "source" {
						m.choice = 1
					}
				} else {
					m.screen = "action"
				}
			case "checks", "review":
				m.screen = "scope"
			case "done":
				m.screen = "welcome"
				return m, loadOverview(m.opts)
			}
			return m, nil
		}
		if key == "up" || key == "k" {
			m.choice = max(0, m.choice-1)
			m.scroll = 0
		}
		if key == "down" || key == "j" || key == "tab" || key == "right" {
			m.choice = (m.choice + 1) % m.choiceCount()
			m.scroll = 0
		}
		if key == "r" && m.screen == "checks" {
			return m, m.begin("check")
		}
		if key == "enter" || key == " " {
			m.scroll = 0
			switch m.screen {
			case "welcome":
				if m.loaded {
					if m.opts.Action == "install" {
						m.screen = "method"
					} else if m.opts.Action != "" {
						m.screen = "scope"
					} else {
						m.screen = "action"
					}
					m.choice = 0
					if m.screen == "method" && m.opts.Method == "source" {
						m.choice = 1
					}
					if m.screen == "scope" && m.opts.Scope == "system" {
						m.choice = 1
					}
				}
			case "action":
				if m.choice == 0 {
					m.opts.Action = "install"
				} else {
					m.opts.Action = "uninstall"
				}
				m.screen = "scope"
				if m.opts.Action == "install" {
					m.screen = "method"
				}
				m.choice = 0
				if m.screen == "method" && m.opts.Method == "source" {
					m.choice = 1
				}
			case "method":
				method := "prebuilt"
				if m.choice == 1 {
					method = "source"
				}
				info := m.overview.Methods[method]
				if !info.Available {
					m.errorText = info.Detail
					return m, nil
				}
				m.opts.Method = method
				m.errorText = ""
				m.screen = "scope"
				m.choice = 0
				if m.opts.Scope == "system" {
					m.choice = 1
				}
			case "scope":
				if m.choice == 0 {
					m.opts.Scope = "user"
				} else {
					m.opts.Scope = "system"
				}
				if m.opts.DryRun {
					m.screen = "review"
					m.success = true
					return m, nil
				}
				return m, m.begin("check")
			case "checks":
				if m.success {
					m.screen = "review"
					m.choice = 0
				} else {
					return m, m.begin("check")
				}
			case "review":
				if m.choice == 0 {
					return m, tea.Quit
				}
				if m.choice == 1 {
					m.screen = "scope"
					m.choice = 0
					return m, nil
				}
				if m.opts.DryRun {
					m.screen = "done"
					m.success = true
					return m, nil
				}
				if m.selectedLayout().Administrator {
					cmd := exec.Command("sudo", "-v")
					return m, tea.ExecProcess(cmd, func(err error) tea.Msg { return authMsg{err} })
				}
				return m, m.begin("execute")
			case "done":
				return m, tea.Quit
			}
		}
	}
	return m, nil
}
func (m model) choiceCount() int {
	if m.screen == "action" || m.screen == "method" || m.screen == "scope" {
		return 2
	}
	if m.screen == "review" {
		return 3
	}
	return 1
}
func (m model) selectedLayout() layout { return m.overview.Layouts[m.opts.Scope] }
func (m model) p() palette             { return colors(m.opts.Theme == "light") }
func (m model) text(s string) string {
	return lipgloss.NewStyle().Foreground(m.p().text).Render(clean(s))
}
func (m model) muted(s string) string {
	return lipgloss.NewStyle().Foreground(m.p().muted).Render(clean(s))
}
func (m model) accent(s string) string {
	return lipgloss.NewStyle().Foreground(m.p().accent).Render(clean(s))
}
func (m model) heading(s string) string {
	return lipgloss.NewStyle().Foreground(m.p().text).Bold(true).Render(clean(s))
}
func (m model) button(s string, selected bool) string {
	st := lipgloss.NewStyle().Padding(0, 2).Foreground(m.p().text).Background(m.p().panel)
	if selected {
		st = st.Foreground(m.p().ink).Background(m.p().accent).Bold(true)
	}
	return st.Render(s)
}
func (m model) card(title, desc string, selected bool, width int) string {
	vertical := 0
	if m.height >= 32 {
		vertical = 1
	}
	st := lipgloss.NewStyle().Width(width-4).Padding(vertical, 2).Border(lipgloss.RoundedBorder()).BorderForeground(m.p().line).Background(m.p().panel)
	marker := "  "
	if selected {
		st = st.BorderForeground(m.p().accent)
		marker = "› "
	}
	return paint(st.Render(m.heading(marker+title)+"\n"+m.muted(desc)), m.p().panel, m.p().text)
}

const logo = ` ██████╗███████╗███████╗ ██████╗ ███╗   ██╗████████╗
██╔════╝██╔════╝██╔════╝██╔═══██╗████╗  ██║╚══██╔══╝
██║     █████╗  █████╗  ██║   ██║██╔██╗ ██║   ██║
██║     ██╔══╝  ██╔══╝  ██║   ██║██║╚██╗██║   ██║
╚██████╗███████╗██║     ╚██████╔╝██║ ╚████║   ██║
 ╚═════╝╚══════╝╚═╝      ╚═════╝ ╚═╝  ╚═══╝   ╚═╝`

func (m model) rail(width, height int) string {
	rows := []string{m.accent("╔═╗"), m.accent("╠╣ ") + m.heading("  CeFont"), m.accent("╚  "), "", m.muted("INSTALLATION STUDIO"), ""}
	names := []string{"Welcome", "Operation", "Method", "Destination", "Preflight", "Review", "Apply"}
	screens := []string{"welcome", "action", "method", "scope", "checks", "review", "progress"}
	if m.opts.Action == "uninstall" {
		names = append(names[:2], names[3:]...)
		screens = append(screens[:2], screens[3:]...)
	}
	phase := len(names) - 1
	for i, name := range screens {
		if name == m.screen {
			phase = i
			break
		}
	}
	for i, name := range names {
		marker := "○"
		if i < phase {
			marker = "✓"
		}
		if i == phase {
			marker = "›"
			rows = append(rows, m.accent(marker+"  "+name))
		} else {
			rows = append(rows, m.muted(marker+"  "+name))
		}
		if height >= 2*len(names)+9 {
			rows = append(rows, "")
		}
	}
	if height >= 33 {
		rows = append(rows, "", m.accent("Aa Bb Cc"), m.muted("0123456789"), m.muted("{} [] <> :="))
	}
	rows = append(rows, "", m.muted("UPDATE / LOCKED"), m.muted("No stream configured"))
	return paint(lipgloss.NewStyle().Width(width).Height(height).Background(m.p().panel).Padding(1, 2).Render(strings.Join(rows, "\n")), m.p().panel, m.p().text)
}
func (m model) content(width int) string {
	gap := "\n\n"
	switch m.screen {
	case "welcome":
		if m.height < 24 {
			gap = "\n"
		} else if m.height >= 30 {
			gap = "\n\n\n"
		}
		brand := m.accent(logo)
		if width < 54 {
			brand = m.accent("╭──────────────────╮\n│  C e F o n t     │\n╰──────────────────╯")
		}
		intro := m.heading("Your terminal. Your type.") + "\n" + m.muted("A dedicated home for CeFont, ready in a few steps.")
		user, system := m.overview.Layouts["user"], m.overview.Layouts["system"]
		status := m.muted("CURRENT USER  / "+user.Status) + "\n" + m.muted("SYSTEM        / "+system.Status)
		if m.height >= 30 {
			status = m.muted("CURRENT USER  / "+user.Status) + "\n\n" + m.muted("SYSTEM        / "+system.Status)
		}
		if !m.loaded {
			status = m.spin.View() + " Reading installation status…"
		}
		if m.errorText != "" {
			status = m.text(m.errorText)
		}
		return brand + gap + intro + gap + status + gap + m.button("Begin setup  →", true) + "  " + m.muted("Enter to begin")
	case "action":
		return m.heading("What brings you here?") + "\n" + m.muted("Choose how you want to manage CeFont.") + gap + m.card("Install CeFont", "Install the prebuilt release, or build your local patches.", m.choice == 0, width) + "\n" + m.card("Remove CeFont", "Remove a verified installation. Keep personal fonts and settings.", m.choice == 1, width) + gap + m.muted("⊘  Update is locked until an update stream is available.")
	case "method":
		prebuilt, source := m.overview.Methods["prebuilt"], m.overview.Methods["source"]
		title := "Prebuilt release / recommended"
		if prebuilt.Version != "" {
			title += " / " + prebuilt.Version
		}
		if !prebuilt.Available {
			title += " / unavailable"
		}
		sourceTitle := "Build local source"
		if !source.Available {
			sourceTitle += " / unavailable"
		}
		body := m.heading("How would you like to install?") + "\n" + m.muted("Prebuilt installs the release. Local source includes your patches.") + gap +
			m.card(title, prebuilt.Detail, m.choice == 0, width) + "\n" +
			m.card(sourceTitle, source.Detail, m.choice == 1, width)
		if m.errorText != "" {
			body += gap + m.text(m.errorText)
		}
		return body
	case "scope":
		user, system := m.overview.Layouts["user"], m.overview.Layouts["system"]
		ud := "Only your account. No administrator access.\n" + user.Command + "\n" + user.Status
		sd := "Shared command for all accounts. Administrator access may be required.\n" + system.Command + "\n" + system.Status
		if user.Error != "" {
			ud = user.Error
		}
		if system.Error != "" {
			sd = system.Error
		}
		return m.heading("Choose a home for CeFont.") + "\n" + m.muted("The destination is reviewed before anything is applied.") + gap + m.card("Current user", ud, m.choice == 0, width) + "\n" + m.card("System-wide", sd, m.choice == 1, width)
	case "checks":
		title := "Checking your setup…"
		if !m.busy {
			if m.success {
				title = "Ready for review."
			} else {
				title = "A few things need attention."
			}
		}
		rows := []string{m.heading(title), m.muted("Read-only checks. No installation files are created."), ""}
		for _, c := range m.checks {
			marker := "✓"
			if c.Status == "error" {
				marker = "×"
			}
			if c.Status == "pending" {
				marker = "○"
			}
			rows = append(rows, m.accent(marker+" ")+m.heading(c.Label)+"  "+m.text(c.Detail))
			if c.Hint != "" {
				rows = append(rows, m.muted("  "+c.Hint))
			}
		}
		if m.busy {
			rows = append(rows, "", m.spin.View()+" "+m.text(m.current))
		} else {
			label := "Continue to review  →"
			if !m.success {
				label = "Retry checks"
				rows = append(rows, "", m.text(m.errorText))
			}
			rows = append(rows, "", m.button(label, true), m.muted("b Back to destinations"))
		}
		return strings.Join(rows, "\n")
	case "review":
		l := m.selectedLayout()
		access := "Your account's permissions"
		if l.Administrator {
			access = "Administrator authentication after Apply"
		}
		methodSummary := "Prebuilt release (bundled wheel)"
		if m.opts.Method == "source" {
			methodSummary = "Build local source (includes checkout edits)"
		}
		if version := m.overview.Methods[m.opts.Method].Version; version != "" {
			methodSummary += " / " + version
		}
		title := "Review your installation."
		if m.opts.Action == "uninstall" {
			title = "Review removal."
			methodSummary = "Uninstall owned files"
		}

		return m.heading(title) + "\n" + m.muted("Cancel is selected by default. Nothing has been applied.") + gap + m.card(strings.ToUpper(m.opts.Action)+" / "+m.opts.Scope, "Method\n"+methodSummary+"\n\nRuntime\n"+l.Runtime+"\n\nCommand\n"+l.Command+"\n\nAccess\n"+access, false, width) + gap + m.text("Your personal fonts, settings, and caches are preserved.") + "\n" + m.muted("Existing files are never overwritten; ownership checks stay enabled.")
	case "progress":
		title := "Making room for CeFont."
		if m.opts.Action == "uninstall" {
			title = "Removing the owned installation."
		}
		if m.stopping {
			title = "Stopping safely."
		}
		rows := []string{m.heading(title), m.muted(fmt.Sprintf("%s / %s / %ds elapsed", m.opts.Action, m.opts.Scope, int(time.Since(m.started).Seconds()))), ""}
		for _, s := range m.completed {
			rows = append(rows, m.accent("✓ ")+m.text(s))
		}
		activity := m.spin.View()
		if m.opts.NoAnimation {
			activity = "›"
		}
		rows = append(rows, "", activity+" "+m.heading(m.current), "", m.muted("q requests a safe stop. Cleanup finishes before exit."))
		return strings.Join(rows, "\n")
	case "done":
		if !m.success {
			return m.accent("╭── × ──╮\n╰───────╯") + gap + m.heading("Setup stopped.") + gap + m.text(m.errorText) + gap + m.muted("Review the failure before retrying. Partial operations may need attention.") + gap + m.button("Close", true) + "  " + m.muted("b Return to setup")
		}
		title := "CeFont is ready."
		details := "Try: " + m.selectedLayout().Command + " --help"
		if m.pathStatus != "" {
			details += "\n" + m.pathStatus
		}
		if m.opts.Action == "uninstall" {
			title = "CeFont was removed."
			details = "Your personal fonts, settings, and caches were preserved."
		}
		if m.opts.DryRun {
			title = "Plan reviewed."
			details = "Dry run finished. No installation files were changed."
		}
		return m.accent("╭────────╮\n│   ✓    │\n╰────────╯") + gap + m.heading(title) + gap + m.text(details) + gap + m.muted("Runtime: "+m.selectedLayout().Runtime) + gap + m.button("Close setup", true)
	}
	return ""
}
func (m model) View() tea.View {
	p := m.p()
	base := lipgloss.NewStyle().Background(p.bg).Foreground(p.text)
	if m.width < 60 || m.height < 20 {
		v := tea.NewView(base.Width(max(1, m.width)).Height(max(1, m.height)).Render("CeFont setup\n\nResize to at least 60 columns × 20 rows.\nq cancels safely."))
		v.AltScreen = true
		return v
	}
	padding, top := 2, 0
	if m.width >= 100 {
		padding = 4
	}
	if m.height >= 32 {
		top = 2
	} else if m.height >= 28 {
		top = 1
	}
	iw, bh := m.width-2*padding, m.height-6-top
	rw := 0
	if m.width >= 100 {
		rw = 28
	}
	mw := iw - rw
	if rw > 0 {
		mw -= 6
	}
	vp := viewport.New(viewport.WithWidth(mw), viewport.WithHeight(bh))
	vp.SetContent(lipgloss.NewStyle().Width(mw).Render(m.content(mw)))
	offset := m.scroll
	if offset == 0 && (m.screen == "scope" || m.screen == "action" || m.screen == "method") && m.choice > 0 {
		lines := strings.Split(ansi.Strip(m.content(mw)), "\n")
		for i, line := range lines {
			if strings.Contains(line, "› ") {
				end := i
				for end+1 < len(lines) && !strings.Contains(lines[end], "╰") {
					end++
				}
				offset = max(0, end-bh+1)
				break
			}
		}
	}
	vp.SetYOffset(offset)
	body := paint(vp.View(), p.bg, p.text)
	if rw > 0 {
		// Paint the gutter separately so the sidebar background cannot fill it.
		gutter := paint(strings.TrimSuffix(strings.Repeat("      \n", bh), "\n"), p.bg, p.text)
		body = lipgloss.JoinHorizontal(lipgloss.Top, m.rail(rw-2, bh-2), gutter, body)
	}
	header := m.accent("CEFONT") + m.muted(" / SETUP STUDIO")
	if m.loaded {
		header += "  " + m.muted(m.overview.Version)
	}
	header = lipgloss.NewStyle().Width(iw).Render(header)
	footer := m.muted("↑↓ / tab Navigate   enter Select   b Back   q Quit   pgup/pgdn Scroll")
	if m.busy {
		footer = m.muted("Working…  q Request safe stop   pgup/pgdn Scroll")
	}
	if m.screen == "review" {
		label := "Apply changes"
		if m.opts.DryRun {
			label = "Finish dry run"
		}
		footer = m.button("Cancel", m.choice == 0) + " " + m.button("Back", m.choice == 1) + " " + m.button(label, m.choice == 2)
	}
	footer = ansi.Truncate(footer, iw, "")
	frame := header + "\n" + m.accent(strings.Repeat("─", iw)) + "\n" + strings.Repeat("\n", top) + body + "\n" + footer
	v := tea.NewView(paint(base.Width(m.width).Height(m.height).Padding(1, padding).MaxWidth(m.width).MaxHeight(m.height).Render(frame), p.bg, p.text))
	v.AltScreen = true
	return v
}
func main() {
	var o options
	flag.StringVar(&o.Backend, "backend", "", "Python executable")
	flag.StringVar(&o.Script, "script", "", "Installer backend")
	flag.StringVar(&o.Python, "python", "", "Target Python executable")
	flag.StringVar(&o.Prefix, "prefix", "", "Custom prefix")
	flag.StringVar(&o.Action, "action", "", "Initial operation")
	flag.StringVar(&o.Scope, "scope", "", "Initial scope")
	flag.StringVar(&o.Method, "method", "prebuilt", "prebuilt or source")
	flag.StringVar(&o.Theme, "theme", "auto", "auto, light, dark")
	flag.BoolVar(&o.NoAnimation, "no-animation", false, "Static indicators")
	flag.BoolVar(&o.DryRun, "dry-run", false, "Review without mutation")
	flag.Parse()
	if o.Backend == "" || !filepath.IsAbs(o.Script) {
		fmt.Fprintln(os.Stderr, "Launch this setup through ./install.sh")
		os.Exit(2)
	}
	if o.Python == "" {
		o.Python = o.Backend
	}
	m := newModel(o)
	program := tea.NewProgram(m)
	result, err := program.Run()
	m.work.close()
	if err != nil {
		fmt.Fprintln(os.Stderr, "Setup UI:", err)
		os.Exit(1)
	}
	if final, ok := result.(model); ok && final.screen == "done" && !final.success {
		os.Exit(1)
	}
}
