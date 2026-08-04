"""17개 시도 GeoJSON(southkorea/southkorea-maps, 2018)을 단순화해 정적 SVG path로 변환.
1회성 스크립트 — 결과 path 문자열을 template.html에 손으로 붙여넣는다 (지리 경계는 안 바뀌므로
매 빌드마다 재생성할 필요 없음).
"""
import json
import math

NAME_SHORT = {
    "서울특별시": "서울", "부산광역시": "부산", "대구광역시": "대구", "인천광역시": "인천",
    "광주광역시": "광주", "대전광역시": "대전", "울산광역시": "울산", "세종특별자치시": "세종",
    "경기도": "경기", "강원도": "강원", "충청북도": "충북", "충청남도": "충남",
    "전라북도": "전북", "전라남도": "전남", "경상북도": "경북", "경상남도": "경남",
    "제주특별자치도": "제주",
}

MIN_RING_DIAG_DEG = 0.10  # 이보다 작은 섬(링)은 시각적으로 무의미해서 제외
RDP_EPS_DEG = 0.006


def rdp(points, eps):
    if len(points) < 3:
        return points
    (x1, y1), (x2, y2) = points[0], points[-1]
    dx, dy = x2 - x1, y2 - y1
    norm = math.hypot(dx, dy)
    max_d, idx = -1, -1
    for i in range(1, len(points) - 1):
        px, py = points[i]
        if norm == 0:
            d = math.hypot(px - x1, py - y1)
        else:
            d = abs(dy * px - dx * py + x2 * y1 - y2 * x1) / norm
        if d > max_d:
            max_d, idx = d, i
    if max_d > eps:
        left = rdp(points[: idx + 1], eps)
        right = rdp(points[idx:], eps)
        return left[:-1] + right
    return [points[0], points[-1]]


def ring_bbox_diag(ring):
    lons = [p[0] for p in ring]
    lats = [p[1] for p in ring]
    return math.hypot(max(lons) - min(lons), max(lats) - min(lats))


def flatten_rings(geom):
    if geom["type"] == "Polygon":
        return [geom["coordinates"][0]]  # 외곽 링만 사용(구멍 무시 — 시군구 exclave 등은 스코프 밖)
    rings = []
    for poly in geom["coordinates"]:
        rings.append(poly[0])
    return rings


data = json.load(open("korea_2018.geojson", encoding="utf-8"))

kept_by_name = {}
all_points = []
for f in data["features"]:
    name = NAME_SHORT[f["properties"]["name"]]
    rings = flatten_rings(f["geometry"])
    kept = [r for r in rings if ring_bbox_diag(r) >= MIN_RING_DIAG_DEG]
    if not kept:
        kept = [max(rings, key=len)]  # 안전장치: 다 걸러지면 가장 큰 링은 유지
    simplified = [rdp(r, RDP_EPS_DEG) for r in kept]
    kept_by_name[name] = simplified
    for ring in simplified:
        all_points.extend(ring)

lons = [p[0] for p in all_points]
lats = [p[1] for p in all_points]
lon_min, lon_max = min(lons), max(lons)
lat_min, lat_max = min(lats), max(lats)
mean_lat_rad = math.radians((lat_min + lat_max) / 2)
cos_lat = math.cos(mean_lat_rad)

WIDTH = 460
proj_x_min = lon_min * cos_lat
proj_x_max = lon_max * cos_lat
scale = WIDTH / (proj_x_max - proj_x_min)
HEIGHT = (lat_max - lat_min) * scale

def project(lon, lat):
    x = (lon * cos_lat - proj_x_min) * scale
    y = (lat_max - lat) * scale
    return round(x, 1), round(y, 1)

path_by_name = {}
total_pts = 0
for name, rings in kept_by_name.items():
    segs = []
    for ring in rings:
        pts = [project(lon, lat) for lon, lat in ring]
        total_pts += len(pts)
        d = "M" + " L".join(f"{x},{y}" for x, y in pts) + " Z"
        segs.append(d)
    path_by_name[name] = " ".join(segs)

print(f"viewBox: 0 0 {WIDTH:.1f} {HEIGHT:.1f}")
print("total points:", total_pts)

with open("korea_paths.json", "w", encoding="utf-8") as fp:
    json.dump({"width": round(WIDTH, 1), "height": round(HEIGHT, 1), "paths": path_by_name}, fp, ensure_ascii=False)
print("wrote korea_paths.json")
