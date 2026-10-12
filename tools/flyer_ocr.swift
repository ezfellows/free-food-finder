// Vision bridge: OCR and QR decoding for event flyers.
//
// Reads image paths from argv (or newline-delimited on stdin when argv is "-")
// and writes one JSON object per line to stdout.
//
// JSONL rather than a single document on purpose: a run handles dozens of
// flyers, and Vision can hard-fault on a malformed JPEG. One object per line,
// flushed immediately, means a fault on image 43 still leaves the other 42
// usable.
//
// Built and cached by ocr.py; see that module for the compile contract.

import Foundation
import Vision
import ImageIO
import CoreGraphics

struct Line: Codable {
    let text: String
    let conf: Double
    let x: Double, y: Double, w: Double, h: Double
}

struct Barcode: Codable {
    let payload: String
    let symbology: String
    let x: Double, y: Double, w: Double, h: Double
}

struct Reading: Codable {
    let path: String
    let ok: Bool
    let width: Int
    let height: Int
    let ocr_rev: Int
    let ms: Int
    let lines: [Line]
    let barcodes: [Barcode]
    let error: String?
}

let encoder = JSONEncoder()

func emit(_ reading: Reading) {
    if let data = try? encoder.encode(reading),
       let text = String(data: data, encoding: .utf8) {
        print(text)
    } else {
        print("{\"path\":\"\",\"ok\":false,\"error\":\"encode failed\"}")
    }
    fflush(stdout)          // a kill mid-run still leaves usable output
}

func pixelSize(_ url: URL) -> (Int, Int) {
    guard let source = CGImageSourceCreateWithURL(url as CFURL, nil),
          let props = CGImageSourceCopyPropertiesAtIndex(source, 0, nil)
            as? [CFString: Any] else { return (0, 0) }
    let w = props[kCGImagePropertyPixelWidth] as? Int ?? 0
    let h = props[kCGImagePropertyPixelHeight] as? Int ?? 0
    return (w, h)
}

func read(_ path: String) -> Reading {
    let started = Date()
    let url = URL(fileURLWithPath: path)
    let revision = VNRecognizeTextRequest.currentRevision
    let (pw, ph) = pixelSize(url)

    guard FileManager.default.fileExists(atPath: path) else {
        return Reading(path: path, ok: false, width: 0, height: 0,
                       ocr_rev: revision, ms: 0, lines: [], barcodes: [],
                       error: "no such file")
    }

    let text = VNRecognizeTextRequest()
    text.recognitionLevel = .accurate
    text.usesLanguageCorrection = true
    text.recognitionLanguages = ["en-US"]

    let codes = VNDetectBarcodesRequest()
    codes.symbologies = [.qr, .aztec, .microQR]

    do {
        // One handler for both requests, so the image is decoded once.
        let handler = VNImageRequestHandler(url: url, options: [:])
        try handler.perform([text, codes])
    } catch {
        return Reading(path: path, ok: false, width: pw, height: ph,
                       ocr_rev: revision,
                       ms: Int(Date().timeIntervalSince(started) * 1000),
                       lines: [], barcodes: [],
                       error: "\(error.localizedDescription)")
    }

    // Vision's boundingBox is normalised with the origin at BOTTOM-left.
    // Convert to distance-from-top here, once, rather than in every caller:
    // getting this wrong makes each flyer read bottom-up and quietly poisons
    // any heuristic that cares where on the page a line sits.
    var lines: [Line] = []
    for case let obs as VNRecognizedTextObservation in text.results ?? [] {
        guard let best = obs.topCandidates(1).first else { continue }
        let box = obs.boundingBox
        lines.append(Line(text: best.string,
                          conf: Double(best.confidence),
                          x: Double(box.minX),
                          y: Double(1.0 - box.maxY),
                          w: Double(box.width),
                          h: Double(box.height)))
    }
    // Reading order: top to bottom, then left to right within a band.
    lines.sort { a, b in
        abs(a.y - b.y) > 0.02 ? a.y < b.y : a.x < b.x
    }

    var barcodes: [Barcode] = []
    for case let obs as VNBarcodeObservation in codes.results ?? [] {
        let box = obs.boundingBox
        barcodes.append(Barcode(payload: obs.payloadStringValue ?? "",
                                symbology: obs.symbology.rawValue,
                                x: Double(box.minX),
                                y: Double(1.0 - box.maxY),
                                w: Double(box.width),
                                h: Double(box.height)))
    }

    return Reading(path: path, ok: true, width: pw, height: ph,
                   ocr_rev: revision,
                   ms: Int(Date().timeIntervalSince(started) * 1000),
                   lines: lines, barcodes: barcodes, error: nil)
}

var paths = Array(CommandLine.arguments.dropFirst())
if paths == ["-"] {
    paths = []
    while let line = readLine(strippingNewline: true) {
        if !line.isEmpty { paths.append(line) }
    }
}

if paths.isEmpty {
    FileHandle.standardError.write(
        "usage: flyer_ocr <image>... | flyer_ocr -\n".data(using: .utf8)!)
    exit(2)
}

for path in paths {
    emit(read(path))
}
