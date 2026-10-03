import Foundation
import Darwin

// A single selected, freshly checked file. No recursive or permanent removal.
func run() throws -> [String: Any] {
    let input = FileHandle.standardInput.readDataToEndOfFile()
    guard let request = try JSONSerialization.jsonObject(with: input) as? [String: Any],
          let path = request["path"] as? String, path.hasPrefix("/"),
          !path.contains("\0"), !path.split(separator: "/").contains(".."),
          let expected = request["signature"] as? [Int64], expected.count == 5 else {
        throw NSError(domain: "媒体整理助手", code: 1,
                      userInfo: [NSLocalizedDescriptionKey: "文件请求无效"])
    }
    var current = ""
    var info = stat()
    for part in path.split(separator: "/") {
        current += "/" + part
        guard lstat(current, &info) == 0, (info.st_mode & mode_t(S_IFMT)) != mode_t(S_IFLNK) else {
            throw NSError(domain: "媒体整理助手", code: 2,
                          userInfo: [NSLocalizedDescriptionKey: "路径已变化或含符号链接，请重新扫描"])
        }
    }
    let actual: [Int64] = [Int64(info.st_dev), Int64(info.st_ino), Int64(info.st_size),
        Int64(info.st_mtimespec.tv_sec) * 1_000_000_000 + Int64(info.st_mtimespec.tv_nsec),
        Int64(info.st_ctimespec.tv_sec) * 1_000_000_000 + Int64(info.st_ctimespec.tv_nsec)]
    guard (info.st_mode & mode_t(S_IFMT)) == mode_t(S_IFREG), actual == expected else {
        throw NSError(domain: "媒体整理助手", code: 3,
                      userInfo: [NSLocalizedDescriptionKey: "文件自确认后发生变化，请重新扫描"])
    }
    var resulting: NSURL?
    try FileManager.default.trashItem(at: URL(fileURLWithPath: path), resultingItemURL: &resulting)
    return ["ok": true, "trashed_path": resulting?.path ?? ""]
}
let result: [String: Any]
do { result = try run() }
catch { result = ["ok": false, "error": error.localizedDescription] }
if let data = try? JSONSerialization.data(withJSONObject: result) {
    FileHandle.standardOutput.write(data)
}
