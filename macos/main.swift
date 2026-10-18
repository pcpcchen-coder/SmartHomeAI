import AppKit
import Foundation
import WebKit

final class HomeApp: NSObject, NSApplicationDelegate {
    var item: NSStatusItem!
    var timer: Timer?
    var window: NSWindow?
    var web: WKWebView?
    let homeURL = URL(string: "http://127.0.0.1:8788")!

    func applicationDidFinishLaunching(_ notification: Notification) {
        NSApp.setActivationPolicy(.regular)
        let window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 1340, height: 900), styleMask: [.titled, .closable, .miniaturizable, .resizable], backing: .buffered, defer: false)
        window.title = "武陵居中控"
        window.minSize = NSSize(width: 850, height: 650)
        window.center()
        window.isReleasedWhenClosed = false
        let browser = WKWebView(frame: window.contentView!.bounds)
        browser.autoresizingMask = [.width, .height]
        window.contentView?.addSubview(browser)
        self.window = window
        self.web = browser
        window.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
        let mainMenu = NSMenu()
        let appMenuItem = NSMenuItem()
        let appMenu = NSMenu()
        let mainOpen = NSMenuItem(title: "開啟武陵居中控", action: #selector(openHome), keyEquivalent: "o")
        mainOpen.target = self
        appMenu.addItem(mainOpen)
        let reload = NSMenuItem(title: "重新載入介面", action: #selector(reloadView), keyEquivalent: "r")
        reload.target = self
        appMenu.addItem(reload)
        appMenu.addItem(.separator())
        let exit = NSMenuItem(title: "結束 App（中控服務繼續）", action: #selector(quitMenu), keyEquivalent: "q")
        exit.target = self
        appMenu.addItem(exit)
        appMenuItem.submenu = appMenu
        mainMenu.addItem(appMenuItem)
        NSApp.mainMenu = mainMenu
        item = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
        item.button?.image = NSImage(systemSymbolName: "house.fill", accessibilityDescription: "武陵居中控")
        item.button?.toolTip = "武陵居中控"
        let menu = NSMenu()
        let title = NSMenuItem(title: "武陵居 HOME OS", action: nil, keyEquivalent: "")
        title.isEnabled = false
        menu.addItem(title)
        menu.addItem(.separator())
        let open = NSMenuItem(title: "開啟中控", action: #selector(openHome), keyEquivalent: "o")
        open.target = self
        menu.addItem(open)
        let restart = NSMenuItem(title: "啟動／檢查服務", action: #selector(startService), keyEquivalent: "r")
        restart.target = self
        menu.addItem(restart)
        menu.addItem(.separator())
        let quit = NSMenuItem(title: "結束 App（中控服務繼續）", action: #selector(quitMenu), keyEquivalent: "q")
        quit.target = self
        menu.addItem(quit)
        item.menu = menu
        startService()
    }

    @objc func startService() {
        guard let resources = Bundle.main.resourceURL else { return }
        item.button?.toolTip = "武陵居：啟動中控中"
        let process = Process()
        process.executableURL = URL(fileURLWithPath: "/opt/homebrew/bin/python3.12")
        process.arguments = ["-m", "backend.launcher"]
        process.currentDirectoryURL = resources.appendingPathComponent("controller")
        var environment = ProcessInfo.processInfo.environment
        environment["PYTHONDONTWRITEBYTECODE"] = "1"
        environment["PYTHONPATH"] = resources.appendingPathComponent("lib").path + ":" + resources.appendingPathComponent("controller").path
        environment["WULING_DATA_DIR"] = FileManager.default.homeDirectoryForCurrentUser.appendingPathComponent("Library/Application Support/WulingHomeControl").path
        process.environment = environment
        process.standardOutput = FileHandle.nullDevice
        process.standardError = FileHandle.nullDevice
        process.terminationHandler = { task in
            DispatchQueue.main.async {
                if task.terminationStatus == 0 {
                    self.item.button?.toolTip = "武陵居中控 · 點擊開啟"
                    self.web?.load(URLRequest(url: self.homeURL))
                } else {
                    self.item.button?.toolTip = "武陵居：服務未能啟動"
                    let alert = NSAlert()
                    alert.messageText = "中控服務尚未啟動"
                    alert.informativeText = "請確認這台 Mac 的 Python 3.12 仍存在，且 8788 連接埠未被其他程式占用。服務紀錄位於 Library/Application Support/WulingHomeControl/service.log。"
                    alert.addButton(withTitle: "知道了")
                    alert.runModal()
                }
            }
        }
        do { try process.run() }
        catch { item.button?.toolTip = "武陵居：Python 執行環境未找到" }
    }
    @objc func openHome() {
        window?.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
        if web?.url == nil { web?.load(URLRequest(url: homeURL)) }
    }
    func applicationShouldHandleReopen(_ sender: NSApplication, hasVisibleWindows flag: Bool) -> Bool {
        openHome()
        return true
    }
    @objc func reloadView() { web?.reload() }
    @objc func quitMenu() { NSApp.terminate(nil) }
}
let app = NSApplication.shared
let delegate = HomeApp()
app.delegate = delegate
app.run()
