// OCR with Apple's Vision framework (built into macOS; no install). Prints JSON lines to stdout:
// [{"text": "...", "conf": 0.98, "box": [x0, top, x1, bottom]}]  with box as fractions of the image, origin top-left.
// Usage: swift pipeline/ocr.swift image.png
// ponytail: macOS only. On Linux/CI use Tesseract (`tesseract img - tsv`) behind the same JSON shape.
import Foundation
import Vision
import AppKit

let path = CommandLine.arguments[1]
guard let image = NSImage(contentsOfFile: path),
      let cg = image.cgImage(forProposedRect: nil, context: nil, hints: nil) else {
    FileHandle.standardError.write("cannot read \(path)\n".data(using: .utf8)!)
    exit(1)
}
let request = VNRecognizeTextRequest()
request.recognitionLevel = .accurate
request.usesLanguageCorrection = false  // numbers matter more than spelling
try VNImageRequestHandler(cgImage: cg, options: [:]).perform([request])
var out: [[String: Any]] = []
for obs in request.results ?? [] {
    guard let best = obs.topCandidates(1).first else { continue }
    let b = obs.boundingBox  // normalised, origin bottom-left
    out.append(["text": best.string, "conf": best.confidence,
                "box": [b.minX, 1 - b.maxY, b.maxX, 1 - b.minY]])
}
let data = try JSONSerialization.data(withJSONObject: out)
FileHandle.standardOutput.write(data)
