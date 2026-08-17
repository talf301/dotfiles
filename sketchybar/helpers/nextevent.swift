import EventKit
import Foundation

// Writes "<title>|<seconds until start>|<meeting URL>" for the next event within 12h to the cache
// file (empty when there is none). EventKit expands recurring events; Calendar.app's
// AppleScript interface does not, which is why this is a compiled helper.

let cacheDir = FileManager.default.homeDirectoryForCurrentUser
    .appendingPathComponent(".cache/sketchybar", isDirectory: true)
let cache = cacheDir.appendingPathComponent("nextevent")

func write(_ s: String) {
    try? FileManager.default.createDirectory(at: cacheDir, withIntermediateDirectories: true)
    let tmp = cache.appendingPathExtension("tmp")
    try? s.write(to: tmp, atomically: true, encoding: .utf8)
    _ = try? FileManager.default.replaceItemAt(cache, withItemAt: tmp)
}

let store = EKEventStore()
let sema = DispatchSemaphore(value: 0)
var granted = false
store.requestFullAccessToEvents { ok, _ in granted = ok; sema.signal() }
sema.wait()

guard granted else {
    FileHandle.standardError.write("no-access\n".data(using: .utf8)!)
    write("")           // hide the item rather than leave a stale countdown
    exit(2)
}

let skip: Set<String> = ["US Holidays", "Holidays in United States", "Birthdays",
                         "Siri Suggestions", "Scheduled Reminders"]
let cals = store.calendars(for: .event).filter { !skip.contains($0.title) }

var result = ""
if !cals.isEmpty {
    let now = Date()
    let pred = store.predicateForEvents(withStart: now.addingTimeInterval(-5 * 60),
                                        end: now.addingTimeInterval(12 * 3600),
                                        calendars: cals)
    let next = store.events(matching: pred)
        .filter { !$0.isAllDay && $0.startDate >= now.addingTimeInterval(-5 * 60) && $0.status != .canceled }
        .min { $0.startDate < $1.startDate }

    if let e = next {
        let title = (e.title ?? "Event").replacingOccurrences(of: "|", with: "-")
        let text = [e.location, e.notes].compactMap { $0 }.joined(separator: "\n")
        let range = NSRange(text.startIndex..., in: text)
        let detectedURL = try? NSDataDetector(types: NSTextCheckingResult.CheckingType.link.rawValue)
            .firstMatch(in: text, range: range)?.url
        let url = (e.url ?? detectedURL)?.absoluteString ?? ""
        result = "\(title)|\(Int(e.startDate.timeIntervalSince(now)))|\(url)"
    }
}
write(result)
print(result)
