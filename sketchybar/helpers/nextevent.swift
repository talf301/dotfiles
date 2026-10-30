import EventKit
import Foundation

// Writes "<title>|<Unix start time>|<meeting URL>" for the next event within 12h to the cache
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
    // Among events at the same start time, prefer accepted over maybe over not yet answered.
    // Events without a current-user attendee (own events) rank as accepted.
    func rank(_ e: EKEvent) -> Int {
        switch e.attendees?.first(where: { $0.isCurrentUser })?.participantStatus {
        case .declined?: return 3
        case .pending?: return 2
        case .tentative?: return 1
        default: return 0
        }
    }
    let next = store.events(matching: pred)
        .filter { !$0.isAllDay && $0.startDate >= now.addingTimeInterval(-5 * 60) && $0.status != .canceled && rank($0) < 3 }
        .min { ($0.startDate, rank($0)) < ($1.startDate, rank($1)) }

    if let e = next {
        let title = (e.title ?? "Event").replacingOccurrences(of: "|", with: "-")
        let text = [e.location, e.notes].compactMap { $0 }.joined(separator: "\n")
        let range = NSRange(text.startIndex..., in: text)
        let detectedURL = try? NSDataDetector(types: NSTextCheckingResult.CheckingType.link.rawValue)
            .firstMatch(in: text, range: range)?.url
        let url = (e.url ?? detectedURL)?.absoluteString ?? ""
        result = "\(title)|\(Int(e.startDate.timeIntervalSince1970))|\(url)"
    }
}
write(result)
print(result)
