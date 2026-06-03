"""
EXIFScope - Python Backend (server.py)  v2.0  [Fixed]
-------------------------------------------------------
Dependencies:
    pip install flask flask-cors pillow piexif

Run:
    python server.py

Server starts at http://127.0.0.1:5000
"""

import io
from flask import Flask, request, jsonify, send_file
from flask_cors import CORS
from PIL import Image
from PIL.ExifTags import TAGS, GPSTAGS
import piexif

app = Flask(__name__)
CORS(app)

# ── Max upload size: 50 MB ──────────────────────
app.config["MAX_CONTENT_LENGTH"] = 50 * 1024 * 1024


# ──────────────────────────────────────────────
# GPS rational → decimal degrees
# ──────────────────────────────────────────────
def rational_to_decimal(rational_list, ref):
    try:
        def r(x):
            return x[0] / x[1] if isinstance(x, tuple) else float(x)
        d, m, s = r(rational_list[0]), r(rational_list[1]), r(rational_list[2])
        val = d + m / 60.0 + s / 3600.0
        if ref in ("S", "W"):
            val = -val
        return round(val, 6)
    except Exception:
        return None


# ──────────────────────────────────────────────
# Extract EXIF tags from PIL Image
# ──────────────────────────────────────────────
def extract_exif(image: Image.Image) -> dict:
    metadata = {}

    # Always-available info
    metadata["File Format"]  = image.format or "Unknown"
    metadata["Image Mode"]   = image.mode
    metadata["Image Width"]  = str(image.width)
    metadata["Image Height"] = str(image.height)
    metadata["Megapixels"]   = f"{round(image.width * image.height / 1_000_000, 2)} MP"

    # Try getexif() first (Pillow 6+), fallback to _getexif()
    try:
        raw_exif = image.getexif()         # returns {} instead of None on missing EXIF
    except AttributeError:
        raw_exif = image._getexif() or {}

    if not raw_exif:
        return metadata

    gps_data = {}

    for tag_id, value in raw_exif.items():
        tag = TAGS.get(tag_id, f"Tag_{tag_id}")

        if tag == "GPSInfo":
            for gps_id, gps_val in value.items():
                gps_tag = GPSTAGS.get(gps_id, f"GPS_{gps_id}")
                gps_data[gps_tag] = gps_val
            continue

        # Safely stringify every value
        try:
            if isinstance(value, bytes):
                value = value.decode("utf-8", errors="replace").strip()
            elif isinstance(value, tuple):
                if len(value) == 2 and all(isinstance(x, int) for x in value):
                    value = f"{value[0]}/{value[1]}"
                else:
                    value = str(value)
            elif isinstance(value, IFDRational if 'IFDRational' in dir() else float):
                value = str(float(value))
            else:
                value = str(value)
        except Exception:
            value = "<unreadable>"

        metadata[tag] = value

    # GPS processing
    if gps_data:
        lat_val = gps_data.get("GPSLatitude")
        lat_ref = gps_data.get("GPSLatitudeRef", "N")
        lon_val = gps_data.get("GPSLongitude")
        lon_ref = gps_data.get("GPSLongitudeRef", "E")

        if lat_val:
            dec = rational_to_decimal(lat_val, lat_ref)
            metadata["GPSLatitude"] = f"{dec}" if dec is not None else str(lat_val)
        if lon_val:
            dec = rational_to_decimal(lon_val, lon_ref)
            metadata["GPSLongitude"] = f"{dec}" if dec is not None else str(lon_val)

        for k, v in gps_data.items():
            if k not in ("GPSLatitude", "GPSLongitude", "GPSLatitudeRef", "GPSLongitudeRef"):
                try:
                    metadata[f"GPS_{k}"] = str(v)
                except Exception:
                    pass

    return metadata


# ──────────────────────────────────────────────
# POST /extract
# ──────────────────────────────────────────────
@app.route("/extract", methods=["POST"])
def extract():
    if "file" not in request.files:
        return jsonify({"error": "No file provided"}), 400

    f        = request.files["file"]
    filename = f.filename or "upload"
    raw      = f.read()          # read once into memory

    try:
        img      = Image.open(io.BytesIO(raw))
        img.load()               # force full decode so errors surface now
        metadata = extract_exif(img)
        return jsonify({"metadata": metadata, "filename": filename})

    except Exception as e:
        # Return basic info even if PIL fails
        return jsonify({
            "metadata": {
                "Filename":  filename,
                "File Size": f"{len(raw):,} bytes",
                "Note":      "Full EXIF extraction failed for this file type.",
                "Error":     str(e),
            },
            "filename": filename
        })


# ──────────────────────────────────────────────
# POST /strip  — remove all metadata, fast path
# ──────────────────────────────────────────────
@app.route("/strip", methods=["POST"])
def strip():
    if "file" not in request.files:
        return jsonify({"error": "No file provided"}), 400

    f        = request.files["file"]
    filename = f.filename or "upload"
    raw      = f.read()

    ext    = filename.rsplit(".", 1)[-1].lower()
    fmt_map = {"jpg": "JPEG", "jpeg": "JPEG", "png": "PNG",
               "tiff": "TIFF", "tif": "TIFF", "bmp": "BMP", "webp": "WEBP"}
    fmt    = fmt_map.get(ext, "JPEG")

    try:
        img    = Image.open(io.BytesIO(raw))
        img.load()

        output = io.BytesIO()

        if fmt == "JPEG":
            # Use piexif to wipe EXIF cleanly and fast
            try:
                img.save(output, format="JPEG", quality=95, exif=b"")
            except Exception:
                img.save(output, format="JPEG", quality=95)
        else:
            # For PNG/BMP/etc — save without any info dict
            clean = Image.new(img.mode, img.size)
            clean.paste(img)      # paste (not putdata) — much faster
            clean.save(output, format=fmt)

        output.seek(0)
        return send_file(
            output,
            mimetype=f"image/{fmt.lower()}",
            as_attachment=True,
            download_name=f"stripped_{filename}"
        )

    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ──────────────────────────────────────────────
# GET /health
# ──────────────────────────────────────────────
@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok", "message": "EXIFScope backend running"})


# ──────────────────────────────────────────────
if __name__ == "__main__":
    print("=" * 50)
    print("  EXIFScope Backend  —  http://127.0.0.1:5000")
    print("=" * 50)
    app.run(debug=True, port=5000, threaded=True)
