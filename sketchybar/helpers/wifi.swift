import CoreLocation
import CoreWLAN
import Foundation

// Writes the current Wi-Fi SSID to the cache file (empty when not on Wi-Fi).
// macOS 15 only returns the SSID to a process authorized for Location Services,
// hence the CLLocationManager dance and the launchd agent that owns this grant.

let cacheDir = FileManager.default.homeDirectoryForCurrentUser
    .appendingPathComponent(".cache/sketchybar", isDirectory: true)
let cache = cacheDir.appendingPathComponent("wifi")

func write(_ s: String) {
    try? FileManager.default.createDirectory(at: cacheDir, withIntermediateDirectories: true)
    let tmp = cache.appendingPathExtension("tmp")
    try? s.write(to: tmp, atomically: true, encoding: .utf8)
    _ = try? FileManager.default.replaceItemAt(cache, withItemAt: tmp)
}

final class Delegate: NSObject, CLLocationManagerDelegate {
    func locationManagerDidChangeAuthorization(_ m: CLLocationManager) {}
}

let mgr = CLLocationManager()
let delegate = Delegate()
mgr.delegate = delegate

if mgr.authorizationStatus == .notDetermined {
    mgr.requestWhenInUseAuthorization()
    let deadline = Date().addingTimeInterval(10)
    while mgr.authorizationStatus == .notDetermined, Date() < deadline {
        RunLoop.current.run(mode: .default, before: Date().addingTimeInterval(0.2))
    }
}

let status = mgr.authorizationStatus
let ssid = CWWiFiClient.shared().interface()?.ssid()

FileHandle.standardError.write("auth=\(status.rawValue) ssid=\(ssid ?? "<nil>")\n".data(using: .utf8)!)
write(ssid ?? "")
print(ssid ?? "")
