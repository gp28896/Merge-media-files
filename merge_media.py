import subprocess
import sys
import os
import csv
import json
import re
from concurrent.futures import ThreadPoolExecutor

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
def norm(path):
    return os.path.abspath(path).replace("\\", "/")


def get_ext(path):
    return os.path.splitext(path)[1].lower()


def validate_inputs(files):
    exts = [get_ext(f["path"]) for f in files]
    if len(set(exts)) != 1:
        raise ValueError("❌ All input files must have the same format")
    return exts[0]


# ----------------------------
# DURATION (for progress bar)
# ----------------------------
def get_duration(file):
    cmd = [
        "ffprobe",
        "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        file
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    return float(result.stdout.strip())


# ----------------------------
# PROGRESS BAR
# ----------------------------
def run_with_progress(cmd, total_duration):
    process = subprocess.Popen(
        cmd,
        stderr=subprocess.PIPE,
        stdout=subprocess.DEVNULL,
        text=True,
        bufsize=1
    )

    time_pattern = re.compile(r"time=(\d+):(\d+):(\d+\.\d+)")

    while True:
        line = process.stderr.readline()
        if not line:
            break

        match = time_pattern.search(line)
        if match:
            h, m, s = match.groups()
            current = int(h) * 3600 + int(m) * 60 + float(s)
            percent = min(current / total_duration, 1.0)

            bar = int(percent * 30)
            print(
                f"\r[{'#'*bar}{'.'*(30-bar)}] {percent*100:.1f}%",
                end=""
            )

    process.wait()
    print()

    if process.returncode != 0:
        raise subprocess.CalledProcessError(process.returncode, cmd)


# ----------------------------
# PARALLEL PREPROCESS
# ----------------------------
def preprocess_file(i, f, is_video):
    """Normalize file (trim + reset timestamps) into temp file"""
    tmp = f"temp_{i}{get_ext(f['path'])}"

    cmd = ["ffmpeg", "-y", "-i", f["path"]]

    if f["start"] is not None:
        cmd.extend(["-ss", str(f["start"]), "-to", str(f["end"])])

    if is_video:
        cmd.extend(["-c:v", "libx264", "-c:a", "aac"])
    else:
        cmd.extend(["-acodec", "libmp3lame", "-ab", "192k"])

    cmd.append(tmp)

    subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)

    return tmp


# ----------------------------
# MERGE
# ----------------------------
def merge(files, output):
    files = [{"path": norm(f["path"]), "start": f["start"], "end": f["end"]} for f in files]
    output = norm(output)

    ext = validate_inputs(files)
    is_video = ext in [".mp4", ".mkv", ".mov", ".avi"]

    print("⚡ Preprocessing files in parallel...")

    with ThreadPoolExecutor() as executor:
        temp_files = list(executor.map(
            lambda args: preprocess_file(*args),
            [(i, f, is_video) for i, f in enumerate(files)]
        ))

    # build concat filter (simple now)
    inputs = []
    cmd = ["ffmpeg"]

    for f in temp_files:
        cmd.extend(["-i", f])

    if is_video:
        n = len(temp_files)
        cmd.extend([
            "-filter_complex",
            f"{''.join([f'[{i}:v][{i}:a]' for i in range(n)])}"
            f"concat=n={n}:v=1:a=1[outv][outa]",
            "-map", "[outv]",
            "-map", "[outa]",
            "-c:v", "libx264",
            "-c:a", "aac",
            output
        ])
    else:
        n = len(temp_files)
        cmd.extend([
            "-filter_complex",
            f"{''.join([f'[{i}:a]' for i in range(n)])}"
            f"concat=n={n}:v=0:a=1[out]",
            "-map", "[out]",
            "-acodec", "libmp3lame",
            "-ab", "192k",
            output
        ])

    total_duration = sum(get_duration(f) for f in temp_files)

    print("🎬 Merging with progress:\n")
    run_with_progress(cmd, total_duration)

    print(f"\n✅ Output created: {output}")

    # cleanup
    for f in temp_files:
        os.remove(f)


# ----------------------------
# CSV / JSON PARSERS
# ----------------------------
def parse_csv(path):
    files = []
    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.reader(f)
        for row in reader:
            if len(row) == 1:
                files.append({"path": row[0], "start": None, "end": None})
            elif len(row) == 3:
                files.append({"path": row[0], "start": float(row[1]), "end": float(row[2])})
            else:
                raise ValueError("Invalid CSV format")
    return files


def parse_json(path):
    with open(path, encoding="utf-8") as f:
        data = json.load(f)

    files = []
    for item in data:
        files.append({
            "path": item["path"],
            "start": item.get("start"),
            "end": item.get("end")
        })
    return files


# ----------------------------
# CLI PARSER
# ----------------------------
def parse_args(args):
    output = args[-1]
    tokens = args[:-1]

    files = []
    i = 0

    while i < len(tokens):
        path = tokens[i]

        if i + 2 < len(tokens):
            try:
                start = float(tokens[i+1])
                end = float(tokens[i+2])
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

        if "--csv" in args:
            idx = args.index("--csv")
            files = parse_csv(args[idx+1])
            output = args[idx+2]

        elif "--json" in args:
            idx = args.index("--json")
            files = parse_json(args[idx+1])
            output = args[idx+2]

        else:
            files, output = parse_args(args)

        merge(files, output)

    except Exception as e:
        print(f"\n❌ Error: {e}")
        print("\nUsage:")
        print("CLI: python merge_media.py f1 [s1 e1] f2 ... out")
        print("CSV: python merge_media.py --csv input.csv out")
        print("JSON: python merge_media.py --json input.json out")
        sys.exit(1)