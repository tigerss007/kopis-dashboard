import json

from PIL import Image, ImageDraw

d = json.load(open("korea_paths.json", encoding="utf-8"))
W, H = int(d["width"]) + 20, int(d["height"]) + 20
img = Image.new("RGB", (W, H), "white")
draw = ImageDraw.Draw(img)

for name, path in d["paths"].items():
    for seg in path.split(" M"):
        seg = seg if seg.startswith("M") else "M" + seg
        seg = seg.replace("M", "").replace(" Z", "").strip()
        pts = []
        for token in seg.split(" L"):
            x, y = token.split(",")
            pts.append((float(x) + 10, float(y) + 10))
        if len(pts) >= 3:
            draw.polygon(pts, outline="black", fill="#cde2fb")
    # centroid label
    xs = [p[0] for seg in path.split(" M") for p in [seg.replace("M", "").replace(" Z", "").strip().split(" L")[0].split(",")]]
    cx = sum(float(x) for x in xs) / len(xs) + 10
    ys = [p[1] for seg in path.split(" M") for p in [seg.replace("M", "").replace(" Z", "").strip().split(" L")[0].split(",")]]
    cy = sum(float(y) for y in ys) / len(ys) + 10
    draw.text((cx, cy), name, fill="red")

img.save("preview_map.png")
print("saved preview_map.png", img.size)
