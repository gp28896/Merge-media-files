import subprocess
import sys
import os
import csv
import json
from concurrent.futures import ThreadPoolExecutor
import uuid


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
# PREPROCESS (VIDEO ONLY SAFE)
# ----------------------------
def preprocess_file(i, f, is_video):
    path = f["path"]

    if not os.path.exists(path):
        raise FileNotFoundError(f"❌ File not found: {path}")

    tmp = f"temp_{i}_{uuid.uuid4().hex[:6]}{get_ext(path)}"

    cmd = ["ffmpeg", "-y", "-i", path]

    if f["start"] is not None:
        cmd.extend(["-ss", str(f["start"]), "-to", str(f["end"])])

    if is_video:
        cmd.extend([
            "-vf", "scale=1280:720,fps=30,format=yuv420p",
            "-af", "aresample=async=1:first_pts=0",
            "-c:v", "libx264",
            "-preset", "veryfast",
            "-c:a", "aac",
            "-movflags", "+faststart"
        ])
    else:
        # 🔥 IMPORTANT: do NOT re-encode audio aggressively
        cmd.extend(["-c", "copy"])

    cmd.append(tmp)

    result = subprocess.run(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True
    )

    if result.returncode != 0:
        print("\n❌ FFmpeg error:\n")
        print(result.stderr)
        raise subprocess.CalledProcessError(result.returncode, cmd)

    return tmp


# ----------------------------
# AUDIO MERGE (NO GAPS)
# ----------------------------
def merge_audio(files, output):
    list_file = "concat_list.txt"

    with open(list_file, "w", encoding="utf-8") as f:
        for file in files:
            f.write(f"file '{file}'\n")

    cmd = [
        "ffmpeg",
        "-y",
        "-f", "concat",
        "-safe", "0",
        "-i", list_file,

        # 🔥 FIX: re-encode to fix timestamps
        "-c:a", "libmp3lame",
        "-b:a", "192k",

        # 🔥 FIX: regenerate timestamps
        "-fflags", "+genpts",

        output
    ]

    subprocess.run(cmd, check=True)

    os.remove(list_file)
# ----------------------------
# VIDEO MERGE
# ----------------------------
def merge_video(temp_files, output):
    cmd = ["ffmpeg"]

    for f in temp_files:
        cmd.extend(["-i", f])

    n = len(temp_files)

    filter_parts = []
    for i in range(n):
        filter_parts.append(
            f"[{i}:v]setpts=PTS-STARTPTS[v{i}]"
        )
        filter_parts.append(
            f"[{i}:a]asetpts=PTS-STARTPTS[a{i}]"
        )

    concat_inputs = "".join([f"[v{i}][a{i}]" for i in range(n)])

    filter_complex = (
        ";".join(filter_parts) +
        f";{concat_inputs}concat=n={n}:v=1:a=1[outv][outa]"
    )

    cmd.extend([
        "-filter_complex", filter_complex,
        "-map", "[outv]",
        "-map", "[outa]",
        "-c:v", "libx264",
        "-preset", "veryfast",
        "-c:a", "aac",
        "-movflags", "+faststart",
        output
    ])

    subprocess.run(cmd, check=True)


# ----------------------------
# MAIN MERGE
# ----------------------------
def merge(files, output):
    files = [{"path": norm(f["path"]), "start": f["start"], "end": f["end"]} for f in files]
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
        merge_video(temp_files, output)
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
        sys.exit(1)