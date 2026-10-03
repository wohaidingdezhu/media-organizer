import Foundation
import ImageIO
import CoreGraphics
import Darwin

// A JSON-lines worker avoids launching ImageIO once per photo. Single-file
// commands remain available for diagnostics and compatibility.
func writeJSON(_ object: [String: Any]) {
    var data = (try? JSONSerialization.data(withJSONObject: object, options: [.sortedKeys]))
        ?? Data("{\"error\":\"Could not encode image result\"}".utf8)
    data.append(10)
    FileHandle.standardOutput.write(data)
}

func emptyResult(_ path: String) -> [String: Any] {
    return ["path": path, "width": NSNull(), "height": NSNull(),
            "date_original": NSNull(), "date_source": NSNull(),
            "dhash": NSNull(), "low_detail": true]
}

// Give ImageIO a provider for the already checked descriptor. Reopening a
// reused /dev/fd URL intermittently fails in persistent worker processes.
func imageSource(_ descriptor: Int32, _ size: off_t,
                 _ options: [CFString: Any]) -> CGImageSource? {
    guard size > 0 else { return nil }
    let owned = fcntl(descriptor, F_DUPFD_CLOEXEC, 0)
    guard owned >= 0 else { return nil }
    var callbacks = CGDataProviderDirectCallbacks(
        version: 0, getBytePointer: nil, releaseBytePointer: nil,
        getBytesAtPosition: { info, buffer, position, count in
            guard let info = info, position >= 0 else { return 0 }
            let fd = Int32(Int(bitPattern: info) - 1)
            var amount = 0
            while amount < count {
                let read = pread(fd, buffer.advanced(by: amount), count - amount,
                                 position + off_t(amount))
                if read < 0 && errno == EINTR { continue }
                if read <= 0 { break }
                amount += read
            }
            return amount
        },
        releaseInfo: { info in
            if let info = info { Darwin.close(Int32(Int(bitPattern: info) - 1)) }
        })
    guard let provider = CGDataProvider(directInfo: UnsafeMutableRawPointer(bitPattern: Int(owned) + 1),
                                        size: size, callbacks: &callbacks) else {
        Darwin.close(owned)
        return nil
    }
    return CGImageSourceCreateWithDataProvider(provider, options as CFDictionary)
}

func inspectImage(_ path: String) -> [String: Any] {
    var result = emptyResult(path)
    func fail(_ message: String) -> [String: Any] {
        result["error"] = message
        return result
    }
    let descriptor = open((path as NSString).expandingTildeInPath,
                          O_RDONLY | O_NOFOLLOW | O_NONBLOCK | O_CLOEXEC)
    guard descriptor >= 0 else { return fail("Cannot open image without following a symbolic link") }
    defer { Darwin.close(descriptor) }
    var fileInfo = stat()
    guard fstat(descriptor, &fileInfo) == 0,
          (fileInfo.st_mode & mode_t(S_IFMT)) == mode_t(S_IFREG) else {
        return fail("Image source is not a regular file")
    }
    let sourceOptions: [CFString: Any] = [kCGImageSourceShouldCache: false]
    guard let source = imageSource(descriptor, fileInfo.st_size, sourceOptions) else {
        return fail("Cannot open image or unsupported image format")
    }
    guard CGImageSourceGetCount(source) > 0 else {
        return fail("Image contains no readable frames")
    }

    let properties = CGImageSourceCopyPropertiesAtIndex(source, 0, sourceOptions as CFDictionary)
        as? [CFString: Any] ?? [:]
    let rawWidth = (properties[kCGImagePropertyPixelWidth] as? NSNumber)?.intValue
    let rawHeight = (properties[kCGImagePropertyPixelHeight] as? NSNumber)?.intValue
    let orientation = (properties[kCGImagePropertyOrientation] as? NSNumber)?.intValue ?? 1
    let swapsAxes = (5...8).contains(orientation)
    if let width = rawWidth, let height = rawHeight, width > 0, height > 0 {
        result["width"] = swapsAxes ? height : width
        result["height"] = swapsAxes ? width : height
    }

    if let exif = properties[kCGImagePropertyExifDictionary] as? [CFString: Any],
       let date = exif[kCGImagePropertyExifDateTimeOriginal] as? String,
       !date.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
        result["date_original"] = date
        result["date_source"] = "exif_original"
    } else if let tiff = properties[kCGImagePropertyTIFFDictionary] as? [CFString: Any],
              let date = tiff[kCGImagePropertyTIFFDateTime] as? String,
              !date.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
        // TIFF DateTime can be an edit time; it is not a capture date.
        result["date_original"] = date
        result["date_source"] = "tiff_datetime"
    }

    // Decode a bounded, correctly oriented first-frame thumbnail.
    let thumbnailOptions: [CFString: Any] = [
        kCGImageSourceCreateThumbnailFromImageAlways: true,
        kCGImageSourceCreateThumbnailWithTransform: true,
        kCGImageSourceThumbnailMaxPixelSize: 512,
        kCGImageSourceShouldCacheImmediately: true
    ]
    guard let thumbnail = CGImageSourceCreateThumbnailAtIndex(source, 0, thumbnailOptions as CFDictionary) else {
        return fail("Cannot decode image thumbnail")
    }
    if result["width"] is NSNull {
        result["width"] = thumbnail.width
        result["height"] = thumbnail.height
    }

    let sampleWidth = 9
    let sampleHeight = 8
    var pixels = [UInt8](repeating: 255, count: sampleWidth * sampleHeight)
    let rendered = pixels.withUnsafeMutableBytes { bytes -> Bool in
        guard let context = CGContext(data: bytes.baseAddress, width: sampleWidth, height: sampleHeight,
                                      bitsPerComponent: 8, bytesPerRow: sampleWidth,
                                      space: CGColorSpaceCreateDeviceGray(),
                                      bitmapInfo: CGImageAlphaInfo.none.rawValue) else { return false }
        context.setFillColor(gray: 1, alpha: 1)
        context.fill(CGRect(x: 0, y: 0, width: sampleWidth, height: sampleHeight))
        context.interpolationQuality = .high
        context.draw(thumbnail, in: CGRect(x: 0, y: 0, width: sampleWidth, height: sampleHeight))
        return true
    }
    guard rendered else { return fail("Cannot create grayscale thumbnail") }

    var hash: UInt64 = 0
    for row in 0..<sampleHeight {
        for column in 0..<(sampleWidth - 1) {
            hash <<= 1
            if pixels[row * sampleWidth + column] > pixels[row * sampleWidth + column + 1] {
                hash |= 1
            }
        }
    }
    let values = pixels.map(Double.init)
    let mean = values.reduce(0, +) / Double(values.count)
    let variance = values.reduce(0) { $0 + ($1 - mean) * ($1 - mean) } / Double(values.count)
    let range = Int(pixels.max() ?? 0) - Int(pixels.min() ?? 0)
    result["low_detail"] = variance < 64 || range < 20 || hash == 0 || hash == UInt64.max
    result["dhash"] = String(format: "%016llx", hash)
    return result
}

func exportThumbnail(_ path: String, _ output: String) -> [String: Any] {
    func fail(_ message: String) -> [String: Any] { return ["path": path, "error": message] }
    let sourceURL = URL(fileURLWithPath: path).standardizedFileURL
    let outputURL = URL(fileURLWithPath: output).standardizedFileURL
    guard sourceURL != outputURL else { return fail("Thumbnail output must differ from source") }
    let descriptor = open(sourceURL.path, O_RDONLY | O_NOFOLLOW | O_NONBLOCK | O_CLOEXEC)
    guard descriptor >= 0 else { return fail("Cannot open image without following a symbolic link") }
    defer { Darwin.close(descriptor) }
    var fileInfo = stat()
    guard fstat(descriptor, &fileInfo) == 0,
          (fileInfo.st_mode & mode_t(S_IFMT)) == mode_t(S_IFREG) else {
        return fail("Image source is not a regular file")
    }
    let options: [CFString: Any] = [kCGImageSourceShouldCache: false]
    guard let image = imageSource(descriptor, fileInfo.st_size, options),
          CGImageSourceGetCount(image) > 0 else { return fail("Cannot open image") }
    let previewOptions: [CFString: Any] = [
        kCGImageSourceCreateThumbnailFromImageAlways: true,
        kCGImageSourceCreateThumbnailWithTransform: true,
        kCGImageSourceThumbnailMaxPixelSize: 256,
        kCGImageSourceShouldCacheImmediately: true
    ]
    guard let preview = CGImageSourceCreateThumbnailAtIndex(image, 0, previewOptions as CFDictionary) else {
        return fail("Cannot decode image preview")
    }
    let outputData = NSMutableData()
    guard let destination = CGImageDestinationCreateWithData(outputData as CFMutableData, "public.png" as CFString, 1, nil) else {
        return fail("Cannot create preview data")
    }
    CGImageDestinationAddImage(destination, preview, nil)
    guard CGImageDestinationFinalize(destination) else { return fail("Cannot encode preview") }
    let outputFD = open(outputURL.path, O_WRONLY | O_CREAT | O_EXCL | O_NOFOLLOW | O_CLOEXEC, mode_t(0o600))
    guard outputFD >= 0 else { return fail("Cannot create a new preview file") }
    let encoded = outputData as Data
    let wrote = encoded.withUnsafeBytes { bytes -> Bool in
        guard let base = bytes.baseAddress else { return false }
        var position = 0
        while position < bytes.count {
            let count = Darwin.write(outputFD, base.advanced(by: position), bytes.count - position)
            if count <= 0 { return false }
            position += count
        }
        return true
    }
    let closed = Darwin.close(outputFD) == 0
    if !wrote || !closed {
        Darwin.unlink(outputURL.path)
        return fail("Cannot save preview file")
    }
    return ["path": path, "thumbnail": output]
}

let arguments = CommandLine.arguments
if arguments.count == 2 && arguments[1] == "--batch" {
    while let line = readLine() {
        guard let data = line.data(using: .utf8),
              let request = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any],
              let path = request["path"] as? String, !path.isEmpty else {
            writeJSON(["error": "Invalid image request"])
            continue
        }
        if let output = request["thumbnail"] as? String {
            writeJSON(exportThumbnail(path, output))
        } else {
            writeJSON(inspectImage(path))
        }
    }
    exit(0)
}
if arguments.count == 4 && arguments[1] == "--thumbnail" {
    let result = exportThumbnail(arguments[2], arguments[3])
    writeJSON(result)
    exit(result["error"] == nil ? 0 : 1)
}
if arguments.count == 2 && !arguments[1].isEmpty {
    let result = inspectImage(arguments[1])
    writeJSON(result)
    exit(result["error"] == nil ? 0 : 1)
}
writeJSON(["error": "Usage: image_probe IMAGE_FILE | --thumbnail IMAGE_FILE OUTPUT | --batch"])
exit(1)
