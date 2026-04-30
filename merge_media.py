import subprocess
import sys
import os
import csv
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

python merge_media.py \
a.mp3 0 27.4 \
b.mp3 \
c.mp3 5 15 \
output.mp3


python merge_media.py --csv C:/path/input.csv output.mp3


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


def build_filter(files, is_video):
    parts = []

    # AUDIO filters
    for i, f in enumerate(files):
        if f["start"] is not None:
            parts.append(
                f"[{i}:a]atrim={f['start']}:{f['end']},asetpts=PTS-STARTPTS[a{i}]"
            )
        else:
            parts.append(
                f"[{i}:a]aresample=async=1:first_pts=0[a{i}]"
            )

    # VIDEO filters (if needed)
    if is_video:
        for i, f in enumerate(files):
            if f["start"] is not None:
                parts.append(
                    f"[{i}:v]trim={f['start']}:{f['end']},setpts=PTS-STARTPTS[v{i}]"
                )
            else:
                parts.append(
                    f"[{i}:v]setpts=PTS-STARTPTS[v{i}]"
                )

        v_inputs = "".join([f"[v{i}]" for i in range(len(files))])
        a_inputs = "".join([f"[a{i}]" for i in range(len(files))])

        parts.append(
            f"{v_inputs}{a_inputs}concat=n={len(files)}:v=1:a=1[outv][outa]"
        )
    else:
        a_inputs = "".join([f"[a{i}]" for i in range(len(files))])
        parts.append(
            f"{a_inputs}concat=n={len(files)}:v=0:a=1[out]"
        )

    return ";".join(parts)


def merge(files, output):
    files = [{"path": norm(f["path"]), "start": f["start"], "end": f["end"]} for f in files]
    output = norm(output)

    ext = validate_inputs(files)
    is_video = ext in [".mp4", ".mkv", ".mov", ".avi"]

    filter_complex = build_filter(files, is_video)

    cmd = ["ffmpeg"]

    for f in files:
        cmd.extend(["-i", f["path"]])

    cmd.extend(["-filter_complex", filter_complex])

    if is_video:
        cmd.extend([
            "-map", "[outv]",
            "-map", "[outa]",
            "-c:v", "libx264",
            "-c:a", "aac"
        ])
    else:
        cmd.extend([
            "-map", "[out]",
            "-acodec", "libmp3lame",
            "-ab", "192k"
        ])

    cmd.append(output)

    print("\nRunning:")
    print(" ".join(cmd), "\n")

    subprocess.run(cmd, check=True)
    print(f"✅ Output created: {output}")


# ----------------------------
# CLI ARG PARSER (existing)
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
# CSV PARSER (new)
# ----------------------------
def parse_csv(csv_path):
    files = []

    with open(csv_path, newline="", encoding="utf-8") as f:
        reader = csv.reader(f)

        for row_num, row in enumerate(reader, start=1):
            if len(row) == 0:
                continue

            if len(row) == 1:
                # only file path
                files.append({
                    "path": row[0].strip(),
                    "start": None,
                    "end": None
                })

            elif len(row) == 3:
                try:
                    files.append({
                        "path": row[0].strip(),
                        "start": float(row[1]),
                        "end": float(row[2])
                    })
                except:
                    raise ValueError(f"❌ Invalid times in CSV at line {row_num}")

            else:
                raise ValueError(
                    f"❌ Invalid CSV format at line {row_num}. Use: path OR path,start,end"
                )

    return files


# ----------------------------
# ENTRY POINT
# ----------------------------
if __name__ == "__main__":
    try:
        args = sys.argv[1:]

        # CSV mode
        if "--csv" in args:
            idx = args.index("--csv")
            csv_path = args[idx + 1]
            output = args[idx + 2]

            files = parse_csv(csv_path)

        else:
            # CLI mode
            files, output = parse_args(args)

        merge(files, output)

    except Exception as e:
        print(f"\n❌ Error: {e}\n")
        print("Usage:\n")
        print("CLI mode:")
        print("python merge_media.py file1 [s1 e1] file2 [s2 e2] ... output\n")
        print("CSV mode:")
        print("python merge_media.py --csv input.csv output\n")
        print("CSV format:")
        print("path,start,end  OR  path\n")
        sys.exit(1)