"""
-> A general-purpose concat CLI.
    -> variable number of inputs
    -> optional trim per file
    -> enforce same file type
    -> support audio and video
    -> output format matches input

-> It is mandatory to specify absolute path of the output file including the extension.
-> Input file types must match the output file type.
-> If start time for a file is specified, its end time must also be specified

Sample usage:

-> CLI input mode:
python merge_media.py \
a.mp3 0 27.4 \
b.mp3 \
c.mp3 5 15 \
output.mp3

-> csv input mode:
python merge_media.py --csv C:/path/input.csv output.mp3
-> SAMPLE CSV:
<media_file_1_absolute_path>, <start_time_1>, <end_time_1> 
<media_file_2_absolute_path>, <start_time_2>, <end_time_2> 
...

-> JSON input mode:
python merge_media.py --json input.json output.mp3
-> SAMPLE JSON:
[
  { "path": "C:/a.mp3", "start": 0, "end": 10 },
  { "path": "C:/b.mp3" },
  { "path": "C:/c.mp3", "start": 5, "end": 20 }
]


"""
import subprocess
import sys
import os
import csv
import json
from concurrent.futures import ThreadPoolExecutor
import uuid


# ----------------------------
# UTIL
# ----------------------------
def norm(path):
    return os.path.abspath(path).replace("\\", "/")


def get_ext(path):
    return os.path.splitext(path)[1].lower()


def has_nvenc():
    try:
        result = subprocess.run(
            ["ffmpeg", "-hide_banner", "-encoders"],
            capture_output=True,
            text=True
        )
        return "h264_nvenc" in result.stdout
    except:
        return False


# ----------------------------
# VALIDATION
# ----------------------------
def validate_inputs(files):
    exts = [get_ext(f["path"]) for f in files]
    if len(set(exts)) != 1:
        raise ValueError("❌ All input files must have the same format")
    return exts[0]


# ----------------------------
# METADATA HELPERS
# ----------------------------
def get_resolution(file):
    cmd = [
        "ffprobe", "-v", "error",
        "-select_streams", "v:0",
        "-show_entries", "stream=width,height",
        "-of", "csv=p=0"
    ]
    result = subprocess.run(cmd + [file], capture_output=True, text=True)
    w, h = result.stdout.strip().split(",")
    return int(w), int(h)


def has_audio_stream(file):
    cmd = [
        "ffprobe", "-v", "error",
        "-select_streams", "a",
        "-show_entries", "stream=index",
        "-of", "csv=p=0",
        file
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    return result.stdout.strip() != ""


# ----------------------------
# PREPROCESS
# ----------------------------
def preprocess_file(i, f, is_video):
    path = f["path"]

    if not os.path.exists(path):
        raise FileNotFoundError(f"❌ File not found: {path}")

    tmp = f"temp_{i}_{uuid.uuid4().hex[:6]}{get_ext(path)}"

    cmd = ["ffmpeg", "-y"]

    # fast seek
    if f["start"] is not None:
        cmd.extend(["-ss", str(f["start"])])

    cmd.extend(["-i", path])

    if f["end"] is not None:
        cmd.extend(["-to", str(f["end"])])

    if is_video:
        # CPU preprocess only (safe + stable)
        cmd.extend([
            "-vf", "fps=30,format=yuv420p",
            "-af", "aresample=async=1:first_pts=0",
            "-c:v", "libx264",
            "-preset", "veryfast",
            "-crf", "23",
            "-c:a", "aac",
            "-movflags", "+faststart"
        ])
    else:
        cmd.extend(["-c", "copy"])

    cmd.append(tmp)

    subprocess.run(cmd, check=True)
    return tmp


# ----------------------------
# AUDIO MERGE
# ----------------------------
def merge_audio(files, output):
    list_file = "concat_list.txt"

    with open(list_file, "w", encoding="utf-8") as f:
        for file in files:
            f.write(f"file '{file}'\n")

    cmd = [
        "ffmpeg", "-y",
        "-f", "concat",
        "-safe", "0",
        "-i", list_file,
        "-c:a", "libmp3lame",
        "-b:a", "192k",
        "-fflags", "+genpts",
        output
    ]

    subprocess.run(cmd, check=True)
    os.remove(list_file)


# ----------------------------
# VIDEO MERGE (GPU ENABLED)
# ----------------------------
def merge_video(temp_files, output, use_gpu=False):

    cmd = ["ffmpeg", "-y"]

    for f in temp_files:
        cmd.extend(["-i", f])

    n = len(temp_files)

    resolutions = [get_resolution(f) for f in temp_files]
    max_w = max(w for w, _ in resolutions)
    max_h = max(h for _, h in resolutions)

    filter_parts = []
    concat_inputs = ""

    for i in range(n):
        has_audio = has_audio_stream(temp_files[i])

        filter_parts.append(
            f"[{i}:v]"
            f"scale={max_w}:{max_h}:force_original_aspect_ratio=decrease,"
            f"pad={max_w}:{max_h}:(ow-iw)/2:(oh-ih)/2,"
            f"setsar=1,setpts=PTS-STARTPTS[v{i}]"
        )

        if has_audio:
            filter_parts.append(
                f"[{i}:a]aresample=async=1:first_pts=0,asetpts=PTS-STARTPTS[a{i}]"
            )
        else:
            filter_parts.append(
                f"anullsrc=channel_layout=stereo:sample_rate=44100[a{i}]"
            )

        concat_inputs += f"[v{i}][a{i}]"

    filter_complex = (
        ";".join(filter_parts)
        + f";{concat_inputs}concat=n={n}:v=1:a=1[outv][outa]"
    )

    if use_gpu and has_nvenc():
        video_codec = ["-c:v", "h264_nvenc", "-preset", "p4", "-cq", "23"]
        print("🚀 Using GPU (NVENC)")
    else:
        video_codec = ["-c:v", "libx264", "-preset", "veryfast", "-crf", "23"]
        print("🖥️ Using CPU encoder")

    cmd.extend([
        "-filter_complex", filter_complex,
        "-map", "[outv]",
        "-map", "[outa]",
        *video_codec,
        "-c:a", "aac",
        "-shortest",
        "-movflags", "+faststart",
        output
    ])

    subprocess.run(cmd, check=True)


# ----------------------------
# MERGE CORE
# ----------------------------
def merge(files, output, use_gpu=False):

    files = [
        {
            "path": norm(f["path"]),
            "start": f.get("start"),
            "end": f.get("end")
        }
        for f in files
    ]

    output = norm(output)

    ext = validate_inputs(files)
    is_video = ext in [".mp4", ".mkv", ".mov", ".avi"]

    print("⚡ Preprocessing files...")

    with ThreadPoolExecutor(max_workers=os.cpu_count()) as executor:
        temp_files = list(executor.map(
            lambda args: preprocess_file(*args),
            [(i, f, is_video) for i, f in enumerate(files)]
        ))

    print("🎬 Merging files...")

    if is_video:
        merge_video(temp_files, output, use_gpu)
    else:
        merge_audio(temp_files, output)

    print(f"✅ Output created: {output}")

    for f in temp_files:
        try:
            os.remove(f)
        except:
            pass


# ----------------------------
# PARSERS
# ----------------------------
def parse_csv(path):
    files = []
    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.reader(f)
        for row in reader:
            if len(row) == 1:
                files.append({"path": row[0], "start": None, "end": None})
            elif len(row) == 3:
                files.append({
                    "path": row[0],
                    "start": float(row[1]),
                    "end": float(row[2])
                })
            else:
                raise ValueError("Invalid CSV format")
    return files


def parse_json(path):
    with open(path, encoding="utf-8") as f:
        data = json.load(f)

    return [
        {
            "path": item["path"],
            "start": item.get("start"),
            "end": item.get("end")
        }
        for item in data
    ]


def parse_args(args):
    output = args[-1]
    tokens = args[:-1]

    files = []
    i = 0

    while i < len(tokens):
        path = tokens[i]

        if i + 2 < len(tokens):
            try:
                start = float(tokens[i + 1])
                end = float(tokens[i + 2])
                files.append({"path": path, "start": start, "end": end})
                i += 3
                continue
            except:
                pass

        files.append({"path": path, "start": None, "end": None})
        i += 1

    return files, output


# ----------------------------
# ENTRY
# ----------------------------
if __name__ == "__main__":
    try:
        args = sys.argv[1:]

        use_gpu = False
        if "--gpu" in args:
            use_gpu = True
            args.remove("--gpu")

        if "--csv" in args:
            idx = args.index("--csv")
            files = parse_csv(args[idx + 1])
            output = args[idx + 2]

        elif "--json" in args:
            idx = args.index("--json")
            files = parse_json(args[idx + 1])
            output = args[idx + 2]

        else:
            files, output = parse_args(args)

        merge(files, output, use_gpu)

    except Exception as e:
        print(f"\n❌ Error: {e}")
        sys.exit(1)