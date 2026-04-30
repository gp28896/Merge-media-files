import subprocess
import sys
import os
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
    filter_parts = []

    for i, f in enumerate(files):
        label = f"a{i}"

        if f["start"] is not None:
            part = f"[{i}:a]atrim={f['start']}:{f['end']},asetpts=PTS-STARTPTS[{label}]"
        else:
            part = f"[{i}:a]aresample=async=1:first_pts=0[{label}]"

        filter_parts.append(part)

    concat_inputs = "".join([f"[a{i}]" for i in range(len(files))])

    if is_video:
        # handle both video + audio
        v_parts = []
        for i, f in enumerate(files):
            if f["start"] is not None:
                v_parts.append(f"[{i}:v]trim={f['start']}:{f['end']},setpts=PTS-STARTPTS[v{i}]")
            else:
                v_parts.append(f"[{i}:v]setpts=PTS-STARTPTS[v{i}]")

        filter_parts.extend(v_parts)

        v_concat_inputs = "".join([f"[v{i}]" for i in range(len(files))])

        filter_parts.append(
            f"{v_concat_inputs}{concat_inputs}concat=n={len(files)}:v=1:a=1[outv][outa]"
        )
    else:
        filter_parts.append(
            f"{concat_inputs}concat=n={len(files)}:v=0:a=1[out]"
        )

    return ";".join(filter_parts)


def merge(files, output):
    files = [{"path": norm(f["path"]), "start": f["start"], "end": f["end"]} for f in files]
    output = norm(output)

    ext = validate_inputs(files)
    is_video = ext in [".mp4", ".mkv", ".mov", ".avi"]

    filter_complex = build_filter(files, is_video)

    cmd = ["ffmpeg"]

    # inputs
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


def parse_args(args):
    """
    Pattern:
    file [start end] file [start end] ... output
    """
    if len(args) < 3:
        raise ValueError("Not enough arguments")

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

                files.append({
                    "path": path,
                    "start": start,
                    "end": end
                })
                i += 3
                continue
            except:
                pass

        # no trim
        files.append({
            "path": path,
            "start": None,
            "end": None
        })
        i += 1

    return files, output


if __name__ == "__main__":
    try:
        files, output = parse_args(sys.argv[1:])
        merge(files, output)

    except Exception as e:
        print(f"\n❌ Error: {e}\n")
        print("Usage:")
        print("python merge_media.py file1 [s1 e1] file2 [s2 e2] ... output\n")
        print("Examples:")
        print("python merge_media.py a.mp3 0 10 b.mp3 5 20 c.mp3 out.mp3")
        print("python merge_media.py a.mp4 b.mp4 c.mp4 out.mp4\n")
        sys.exit(1)