import Foundation
import AVFoundation
import ImageIO
import CoreGraphics
import Darwin

// Report-only still frame. The original video is opened read-only and never changed.
func respond(_ value: [String: Any]) {
    var data = (try? JSONSerialization.data(withJSONObject: value)) ?? Data("{}".utf8)
    data.append(10)
    FileHandle.standardOutput.write(data)
}

func frame(_ path: String, _ output: String) -> [String: Any] {
    func fail(_ message: String) -> [String: Any] { ["path": path, "error": message] }
    let source = URL(fileURLWithPath: path).standardizedFileURL
    let target = URL(fileURLWithPath: output).standardizedFileURL
    guard source != target else { return fail("Output must differ from video") }
    let fd = open(source.path, O_RDONLY | O_NOFOLLOW | O_NONBLOCK | O_CLOEXEC)
    guard fd >= 0 else { return fail("Cannot open video without following a symbolic link") }
    defer { Darwin.close(fd) }
    var before = stat()
    guard fstat(fd, &before) == 0, (before.st_mode & mode_t(S_IFMT)) == mode_t(S_IFREG) else {
        return fail("Video source is not a regular file")
    }
    let asset = AVURLAsset(url: source)
    let duration = CMTimeGetSeconds(asset.duration)
    let seconds = duration.isFinite && duration > 0 ? min(max(duration * 0.1, 0.5), 60) : 0
    let generator = AVAssetImageGenerator(asset: asset)
    generator.appliesPreferredTrackTransform = true
    generator.maximumSize = CGSize(width: 400, height: 600)
    generator.requestedTimeToleranceBefore = CMTime(seconds: 2, preferredTimescale: 600)
    generator.requestedTimeToleranceAfter = CMTime(seconds: 2, preferredTimescale: 600)
    let image: CGImage
    do {
        image = try generator.copyCGImage(at: CMTime(seconds: seconds, preferredTimescale: 600), actualTime: nil)
    } catch {
        return fail("Cannot decode video frame: \(error.localizedDescription)")
    }
    var after = stat()
    guard lstat(source.path, &after) == 0,
          (after.st_mode & mode_t(S_IFMT)) == mode_t(S_IFREG),
          after.st_dev == before.st_dev, after.st_ino == before.st_ino,
          after.st_size == before.st_size, after.st_mtimespec.tv_sec == before.st_mtimespec.tv_sec,
          after.st_mtimespec.tv_nsec == before.st_mtimespec.tv_nsec else {
        return fail("Video changed while generating cover")
    }
    let encoded = NSMutableData()
    guard let destination = CGImageDestinationCreateWithData(encoded as CFMutableData, "public.png" as CFString, 1, nil) else {
        return fail("Cannot create frame image")
    }
    CGImageDestinationAddImage(destination, image, nil)
    guard CGImageDestinationFinalize(destination) else { return fail("Cannot encode frame image") }
    let outFD = open(target.path, O_WRONLY | O_CREAT | O_EXCL | O_NOFOLLOW | O_CLOEXEC, mode_t(0o600))
    guard outFD >= 0 else { return fail("Cannot create report cover") }
    let data = encoded as Data
    let wrote = data.withUnsafeBytes { bytes -> Bool in
        guard let base = bytes.baseAddress else { return false }
        var position = 0
        while position < bytes.count {
            let count = Darwin.write(outFD, base.advanced(by: position), bytes.count - position)
            if count <= 0 { return false }
            position += count
        }
        return true
    }
    let closed = Darwin.close(outFD) == 0
    if !wrote || !closed {
        Darwin.unlink(target.path)
        return fail("Cannot save report cover")
    }
    return ["path": path, "thumbnail": output]
}

if CommandLine.arguments.count == 2 && CommandLine.arguments[1] == "--batch" {
    while let line = readLine() {
        guard let data = line.data(using: .utf8),
              let item = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any],
              let path = item["path"] as? String, let output = item["thumbnail"] as? String else {
            respond(["error": "Invalid video cover request"])
            continue
        }
        respond(frame(path, output))
    }
    exit(0)
}
respond(["error": "Usage: video_cover --batch"])
exit(1)
