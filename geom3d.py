# -*- coding: utf-8 -*-
"""«Настоящий» 3D для плеера без 3D-движка.

Корпус = картинка-лицо + толщина. Толщину строим так: по прозрачности картинки
обходим контур, упрощаем его, и каждый отрезок контура — это боковая грань
(прямоугольник от лица до задней стороны). Грани освещаем по нормали, лицо
рисуем с перспективой. Всё ядро — на чистом Python (без Qt), чтобы его можно
было проверить отдельно.
"""
import math

LIGHT = (-0.45, -0.55, 0.70)          # свет сверху-слева-спереди
_ln = math.sqrt(sum(c * c for c in LIGHT))
LIGHT = tuple(c / _ln for c in LIGHT)


# ------------------------------------------------------------------ контур
def trace_outline(alpha, w, h, step=4, thr=128):
    """alpha(x, y) -> 0..255 в координатах картинки. Возвращает внешний контур
    (список (x, y) в координатах картинки) самого большого непрозрачного куска."""
    gw, gh = max(2, w // step + 2), max(2, h // step + 2)
    inside = [[False] * gw for _ in range(gh)]
    for gy in range(1, gh - 1):
        y = min(h - 1, (gy - 1) * step + step // 2)
        row = inside[gy]
        for gx in range(1, gw - 1):
            x = min(w - 1, (gx - 1) * step + step // 2)
            row[gx] = alpha(x, y) >= thr
    # самый большой кусок — заливкой
    seen = [[False] * gw for _ in range(gh)]
    best = None
    for sy in range(gh):
        for sx in range(gw):
            if inside[sy][sx] and not seen[sy][sx]:
                comp, stack = [], [(sx, sy)]
                seen[sy][sx] = True
                while stack:
                    cx, cy = stack.pop()
                    comp.append((cx, cy))
                    for nx, ny in ((cx + 1, cy), (cx - 1, cy), (cx, cy + 1), (cx, cy - 1)):
                        if 0 <= nx < gw and 0 <= ny < gh and inside[ny][nx] and not seen[ny][nx]:
                            seen[ny][nx] = True
                            stack.append((nx, ny))
                if best is None or len(comp) > len(best):
                    best = comp
    if not best:
        return []
    keep = set(best)
    grid = lambda x, y: (x, y) in keep
    start = min(best, key=lambda p: (p[1], p[0]))       # самая верхняя-левая клетка
    # обход по соседям Мура (по часовой стрелке в экранных координатах)
    dirs = [(1, 0), (1, 1), (0, 1), (-1, 1), (-1, 0), (-1, -1), (0, -1), (1, -1)]
    contour = [start]
    cur, back = start, 4                                 # пришли «слева»
    for _ in range(len(best) * 4 + 10):
        found = False
        for i in range(8):
            d = (back + 1 + i) % 8
            nx, ny = cur[0] + dirs[d][0], cur[1] + dirs[d][1]
            if grid(nx, ny):
                back = (d + 4) % 8
                cur = (nx, ny)
                found = True
                break
        if not found or cur == start:
            break
        contour.append(cur)
    pts = [((gx - 1) * step + step / 2, (gy - 1) * step + step / 2) for gx, gy in contour]
    return pts


def rdp(points, eps):
    """Упрощение ломаной (Рамер — Дуглас — Пекер) для замкнутого контура."""
    if len(points) < 4:
        return points[:]

    def simplify(pts):
        if len(pts) < 3:
            return pts[:]
        (x1, y1), (x2, y2) = pts[0], pts[-1]
        dx, dy = x2 - x1, y2 - y1
        L = math.hypot(dx, dy) or 1e-9
        idx, dmax = 0, -1.0
        for i in range(1, len(pts) - 1):
            px, py = pts[i]
            d = abs(dy * px - dx * py + x2 * y1 - y2 * x1) / L
            if d > dmax:
                idx, dmax = i, d
        if dmax > eps:
            a = simplify(pts[:idx + 1])
            b = simplify(pts[idx:])
            return a[:-1] + b
        return [pts[0], pts[-1]]

    # режем замкнутый контур в двух самых далёких точках
    far = max(range(len(points)), key=lambda i: (points[i][0] - points[0][0]) ** 2 + (points[i][1] - points[0][1]) ** 2)
    a = simplify(points[:far + 1])
    b = simplify(points[far:] + [points[0]])
    return a[:-1] + b[:-1]


def build_sides(points, alpha, color_at, w, h, inset=7):
    """Отрезки контура с наружными нормалями и цветом края картинки.
    Возвращает [(x1, y1, x2, y2, nx, ny, (r, g, b))]."""
    sides = []
    n = len(points)
    for i in range(n):
        x1, y1 = points[i]
        x2, y2 = points[(i + 1) % n]
        dx, dy = x2 - x1, y2 - y1
        L = math.hypot(dx, dy)
        if L < 0.5:
            continue
        nx, ny = dy / L, -dx / L
        mx, my = (x1 + x2) / 2, (y1 + y2) / 2
        # нормаль должна смотреть наружу (в прозрачное)
        tx, ty = int(mx + nx * 6), int(my + ny * 6)
        if 0 <= tx < w and 0 <= ty < h and alpha(tx, ty) >= 128:
            nx, ny = -nx, -ny
        cx = int(min(w - 1, max(0, mx - nx * inset)))
        cy = int(min(h - 1, max(0, my - ny * inset)))
        sides.append((x1, y1, x2, y2, nx, ny, color_at(cx, cy)))
    return sides


# ------------------------------------------------------------------ проекция
class Projector:
    """Поворот вокруг центра (рысканье yaw — влево/вправо, тангаж pitch — вверх/вниз)
    и перспектива. Координаты — в пикселях картинки, z — к зрителю."""

    def __init__(self, cx, cy, yaw_deg, pitch_deg, focal=2600.0):
        self.cx, self.cy, self.f = cx, cy, focal
        a, b = math.radians(yaw_deg), math.radians(pitch_deg)
        self.ca, self.sa, self.cb, self.sb = math.cos(a), math.sin(a), math.cos(b), math.sin(b)

    def rot(self, x, y, z):
        X, Y, Z = x - self.cx, y - self.cy, z
        X1 = X * self.ca + Z * self.sa
        Z1 = -X * self.sa + Z * self.ca
        Y2 = Y * self.cb - Z1 * self.sb
        Z2 = Y * self.sb + Z1 * self.cb
        return X1, Y2, Z2

    def p(self, x, y, z):
        X, Y, Z = self.rot(x, y, z)
        k = self.f / max(1.0, self.f - Z)
        return self.cx + X * k, self.cy + Y * k, Z

    def normal(self, nx, ny, nz=0.0):
        X1 = nx * self.ca + nz * self.sa
        Z1 = -nx * self.sa + nz * self.ca
        Y2 = ny * self.cb - Z1 * self.sb
        Z2 = ny * self.sb + Z1 * self.cb
        return X1, Y2, Z2


def side_quads(sides, proj, depth):
    """Видимые боковые грани: [(глубина, [(x,y)*4], (r,g,b))] — от дальних к ближним."""
    zf, zb = depth / 2.0, -depth / 2.0
    out = []
    for x1, y1, x2, y2, nx, ny, col in sides:
        rn = proj.normal(nx, ny)
        if rn[2] <= 0.015:                       # грань смотрит от нас
            continue
        a = proj.p(x1, y1, zf)
        b = proj.p(x2, y2, zf)
        c = proj.p(x2, y2, zb)
        d = proj.p(x1, y1, zb)
        lam = max(0.0, rn[0] * LIGHT[0] + rn[1] * LIGHT[1] + rn[2] * LIGHT[2])
        k = 0.42 + 0.58 * lam
        spec = max(0.0, lam) ** 24 * 70            # металлический блик
        r, g, bl = col
        shade = (min(255, int(r * k + spec)), min(255, int(g * k + spec)), min(255, int(bl * k + spec)))
        depth_key = (a[2] + b[2] + c[2] + d[2]) / 4
        out.append((depth_key, [(a[0], a[1]), (b[0], b[1]), (c[0], c[1]), (d[0], d[1])], shade))
    out.sort(key=lambda t: t[0])
    return out


def face_quad(w, h, proj, depth):
    """Куда попадают углы лица (z = +depth/2)."""
    z = depth / 2.0
    return [proj.p(0, 0, z)[:2], proj.p(w, 0, z)[:2], proj.p(w, h, z)[:2], proj.p(0, h, z)[:2]]


# ------------------------------------------------------------------ цилиндр
def cylinder_strips(cx, radius, y0, y1, yaw_deg, pitch_deg, n=56, tex_limit=82.0,
                    band_u=None, cy=None, focal=2600.0, overlap=0.35):
    """Корпус-трубка: картинку-лицо режем на вертикальные полоски по углу на окружности
    и ставим каждую на своё место после поворота. Возвращает список
    (u0, u1, [(x, y)*4], тень, подложка) — u в координатах картинки; тень: <0 темнее,
    >0 светлее, 'edge' — полоска контура; подложка — (u0, u1) гладкого бока под краем картинки."""
    cy = (y0 + y1) / 2 if cy is None else cy
    pr = Projector(cx, cy, 0.0, pitch_deg, focal)
    yaw = yaw_deg
    lim = tex_limit
    s = math.sin
    rad = math.radians
    front_lam = 0.5 + 0.5 * max(0.0, LIGHT[2])
    out = []
    for i in range(n):
        ta = -90.0 + 180.0 * i / n                  # угол на экране (после поворота)
        tb = -90.0 + 180.0 * (i + 1) / n
        wrap = lambda v: (v + 180.0) % 360.0 - 180.0
        mid = wrap((ta + tb) / 2 - yaw)             # тот же кусок поверхности до поворота
        band = band_u or (cx - radius * 0.85, cx - radius * 0.81)
        under = band if 58.0 < abs(mid) <= lim else None   # под краем картинки — подложка-бок
        if abs(mid) <= lim:                         # видна часть исходной картинки
            da = wrap(ta - yaw)
            db = da + (tb - ta)
            ua = cx + radius * s(rad(max(-90.0, min(90.0, da))))
            ub = cx + radius * s(rad(max(-90.0, min(90.0, db))))
        else:                                       # бок, которого нет на картинке — гладкий корпус
            ua, ub = band                              # ровный участок корпуса
        if ub - ua < 0.05:
            ub = ua + 0.05
        quad = []
        oa, ob = max(-90.0, ta - overlap), min(90.0, tb + overlap)    # чуть внахлёст — без щелей
        for ang, yy in ((oa, y0), (ob, y0), (ob, y1), (oa, y1)):
            x3 = cx + radius * s(rad(ang))
            z3 = radius * math.cos(rad(ang))
            px, py, _z = pr.p(x3, yy, z3)
            quad.append((px, py))
        tm = rad((ta + tb) / 2)
        nx, ny, nz = pr.normal(math.sin(tm), 0.0, math.cos(tm))
        lam = 0.5 + 0.5 * max(0.0, nx * LIGHT[0] + ny * LIGHT[1] + nz * LIGHT[2])
        shade = lam / front_lam - 1.0
        if abs((ta + tb) / 2) > 86.5:
            shade = "edge"
        out.append((ua, ub, quad, shade, under))
    return out
