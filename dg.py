"""
桌宠 · 冬瓜（真实照片版）
冬瓜悬浮在桌面最上层，四周透明、没有窗口框。

  - 坐在 Dock 栏上面，自己发呆（会呼吸）、啃瓜子、闲下来会去跑轮
  - 点它的【头】：摸摸头，开心值 +5，它会蹦起来冒爱心
  - 点它的【肚子】：怕痒扭来扭去，开心值 +3
  - 按住左右拖动：沿着 Dock 栏换个位置
  - 鼠标不在冬瓜身上时，周围的透明区域不会挡住你点别的东西
  - 右键（或 Control + 点击）：看饱腹值/开心值、喂瓜子、摸摸头、去跑轮、说再见
  - 饱腹值和开心值会随时间慢慢下降，关掉再打开也会记住
  - 久坐提醒：连续用电脑 45 分钟，它会蹦起来提醒你起身走走，
    点它一下表示"知道了"，重新计时；
    离开电脑超过 5 分钟（没碰鼠标键盘）会自动重新计时

需要：
  1. 安装 PyQt6：  .venv/bin/python -m pip install PyQt6
  2. 把 donggua.png 和这个文件放在同一个文件夹里
"""

import ctypes
import ctypes.util
import json
import math
import os
import random
import re
import subprocess
import sys
import time

from PyQt6.QtCore import QPointF, QRectF, Qt, QTimer
from PyQt6.QtGui import (QColor, QCursor, QFont, QFontDatabase, QFontMetrics,
                         QPainter, QPainterPath, QPen, QPixmap)
from PyQt6.QtWidgets import QApplication, QMenu, QWidget, QWidgetAction

# ===== 可以自己改的设置 =====
NAME = "冬瓜"
PET_WIDTH = 170             # 冬瓜显示多宽（像素）
SIT_MINUTES = 45            # 连续坐多久提醒
AWAY_MINUTES = 5            # 离开电脑多久算"起来活动过了"
SYSTEM_NOTIFY = True        # 提醒时是否同时弹出 Mac 系统通知
HUNGER_MINUTES = 1.5        # 饱腹值每隔几分钟掉 1 点
WHEEL_MINUTES = 15          # 自己去跑轮，两次之间至少隔几分钟
BORED_MINUTES = 2           # 开心值每隔几分钟掉 1 点
LINES = [
    "吱～",
    "瓜子呢？",
    "我在囤粮食",
    "喝口水吧",
    "今天爬墙了吗？",
    "论文写了吗……",
    "我是最圆的仓鼠",
    "看我干嘛",
    "眨眨眼，看看远处～",
]
# ============================

WINDOWS = sys.platform == "win32"
if getattr(sys, "frozen", False):          # 打包成 exe 后：图片在包里，存档放在 exe 旁边
    HERE = sys._MEIPASS
    DATA_DIR = os.path.dirname(sys.executable)
else:
    HERE = DATA_DIR = os.path.dirname(os.path.abspath(__file__))
IMAGE = os.path.join(HERE, "donggua.png")
STATS_FILE = os.path.join(DATA_DIR, "donggua_stats.json")

BROWN = QColor(125, 85, 75)
CREAM = QColor(255, 248, 238)
PINK = QColor(236, 170, 165)
FULL_COLOR = QColor(247, 178, 103)    # 饱腹值：橘色
JOY_COLOR = QColor(238, 130, 150)     # 开心值：粉色

W, H = 280, 340             # 窗口大小（大部分是透明的）
WHEEL_R = 112               # 跑轮半径
WHEEL_DEPTH = 78            # 跑轮的厚度（侧面看到的筒身）
IN_FRAMES, OUT_FRAMES = 45, 52   # 进轮/出轮动画的帧数
WHEEL_STATES = ("wheel_in", "wheel", "wheel_out")


def get_idle_seconds():
    """问系统：键盘鼠标已经多久没被碰过了（秒）"""
    if WINDOWS:
        class LastInput(ctypes.Structure):
            _fields_ = [("cbSize", ctypes.c_uint), ("dwTime", ctypes.c_uint)]
        info = LastInput(ctypes.sizeof(LastInput), 0)
        if not ctypes.windll.user32.GetLastInputInfo(ctypes.byref(info)):
            return None
        ctypes.windll.kernel32.GetTickCount.restype = ctypes.c_uint
        return ((ctypes.windll.kernel32.GetTickCount() - info.dwTime) & 0xFFFFFFFF) / 1000
    try:
        out = subprocess.run(["ioreg", "-c", "IOHIDSystem"],
                             capture_output=True, text=True, timeout=3).stdout
        m = re.search(r'"HIDIdleTime" = (\d+)', out)
        return int(m.group(1)) / 1e9 if m else None
    except Exception:
        return None


def notify(text):
    if SYSTEM_NOTIFY and not WINDOWS:      # Windows 上只用冬瓜的气泡提醒
        try:
            subprocess.Popen(["osascript", "-e",
                              f'display notification "{text}" with title "{NAME}" sound name "Glass"'])
        except Exception:
            pass


def clamp(v):
    return max(0.0, min(100.0, v))


def load_stats():
    """读取上次的饱腹值/开心值，并扣掉关着的这段时间掉的分"""
    try:
        with open(STATS_FILE) as f:
            d = json.load(f)
        gone = (time.time() - d["time"]) / 60
        return (clamp(d["full"] - gone / HUNGER_MINUTES),
                clamp(d["joy"] - gone / BORED_MINUTES), d.get("laps", 0))
    except Exception:
        return 80.0, 80.0, 0


def ease(x):
    """0~1 之间平滑过渡"""
    x = max(0.0, min(1.0, x))
    return x * x * (3 - 2 * x)


def ease_pop(x):
    """弹出来时稍微冲过头再回来"""
    x = max(0.0, min(1.0, x)) - 1
    return 1 + 2.7 * x ** 3 + 1.7 * x ** 2


class StatsCard(QWidget):
    """右键菜单最上面的小卡片：名字、饱腹值、开心值、久坐倒计时"""

    def __init__(self, pet):
        super().__init__()
        self.pet = pet
        self.setFixedSize(230, 136)

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        pet = self.pet
        p.setPen(BROWN)
        p.setFont(QFont(pet.font.family(), 17, QFont.Weight.Bold))
        p.drawText(QPointF(16, 30), f"🐹 {NAME}")
        p.setFont(QFont(pet.font.family(), 11))
        p.setPen(QColor(170, 130, 120))
        left = max(0, math.ceil(SIT_MINUTES - (time.time() - pet.active_since) / 60))
        p.drawText(QPointF(17, 52), f"⏱ 再过 {left} 分钟提醒你休息")
        for i, (label, value, color) in enumerate((("饱腹", pet.full, FULL_COLOR),
                                                   ("开心", pet.joy, JOY_COLOR))):
            y = 70 + i * 32
            p.setPen(BROWN)
            p.setFont(QFont(pet.font.family(), 13))
            p.drawText(QPointF(16, y + 12), label)
            track = QRectF(56, y, 120, 14)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(244, 228, 218))
            p.drawRoundedRect(track, 7, 7)
            p.setBrush(color)
            p.drawRoundedRect(QRectF(56, y, max(14, 120 * value / 100), 14), 7, 7)
            p.setPen(BROWN)
            p.drawText(QPointF(186, y + 12), f"{int(value)}")
        p.end()


MENU_STYLE = """
QMenu {
    background: #FFF8EE; border: 2px solid #ECAAA5; border-radius: 16px;
    padding: 6px 6px 8px 6px;
}
QMenu::item {
    padding: 8px 24px 8px 14px; margin: 1px 4px; border-radius: 10px;
    color: #7D554B; font-size: 14px;
}
QMenu::item:selected { background: #FBDCD8; }
QMenu::separator { height: 1px; background: #F1D9CF; margin: 5px 12px; }
"""


class Pet(QWidget):
    def __init__(self):
        super().__init__()
        # 无边框 + 永远在最上层 + 透明背景 + 不要窗口阴影
        flags = (Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint
                 | Qt.WindowType.NoDropShadowWindowHint)
        if WINDOWS:
            flags |= Qt.WindowType.Tool                # Windows 上不在任务栏显示
        self.setWindowFlags(flags)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setFixedSize(W, H)

        self.pix = QPixmap(IMAGE)
        if self.pix.isNull():
            raise SystemExit(f"找不到 {IMAGE}，请把 donggua.png 放在同一个文件夹里")
        self.scale = PET_WIDTH / self.pix.width()
        self.pet_h = self.pix.height() * self.scale

        fams = set(QFontDatabase.families())
        hand = next((f for f in ("Yuanti SC", "Hannotate SC", "HanziPen SC", "PingFang SC",
                               "Microsoft YaHei UI", "Microsoft YaHei")
                     if f in fams), "")
        self.font = QFont(hand, 14) if hand else QFont()
        self.font.setPointSize(14)

        area = QApplication.primaryScreen().availableGeometry()
        self.area = area
        self.px = area.center().x() - W / 2
        self.py = area.bottom() - H + 10
        self.move(int(self.px), int(self.py))

        self.state, self.timer = "idle", 80
        self.t = 0
        self.wheel_angle = 0.0
        self.text, self.text_timer = "", 0
        self.floats = []             # 飘起来的爱心和 +5 之类的小字
        self.drag = None
        self.active_since = time.time()
        self.full, self.joy, self.laps = load_stats()
        self.run_len = 300
        self.last_wheel = time.time()

        self.say(f"我是{NAME}！", 90)

        self.anim = QTimer(self)
        self.anim.timeout.connect(self.step)
        self.anim.start(33)                       # 约 30 帧/秒
        self.slow = QTimer(self)
        self.slow.timeout.connect(self.every_10s)
        self.slow.start(10000)

    # ---------- 说话 / 状态 / 数值 ----------
    def set_click_through(self, on):
        if on != getattr(self, "_through", None):
            self._through = on
            if WINDOWS:
                user32, hwnd = ctypes.windll.user32, int(self.winId())
                style = user32.GetWindowLongW(hwnd, -20)          # GWL_EXSTYLE
                style = style | 0x80020 if on else style & ~0x20  # WS_EX_LAYERED|TRANSPARENT
                user32.SetWindowLongW(hwnd, -20, style)
                return
            nswindow = objc_send(int(self.winId()), "window")
            if nswindow:
                objc_send(nswindow, "setIgnoresMouseEvents:", on, ctypes.c_bool)

    def say(self, text, frames=100):
        self.text, self.text_timer = text, frames

    def set_state(self, s, frames):
        self.state, self.timer = s, frames

    def change(self, full=0, joy=0):
        """改饱腹值/开心值，加分时冒出小字"""
        self.full, self.joy = clamp(self.full + full), clamp(self.joy + joy)
        top = self.pet_rect().top()
        if full > 0:
            self.floats.append([W / 2 - 40, top + 20, 1.0, f"+{full} 饱腹", FULL_COLOR])
        if joy > 0:
            self.floats.append([W / 2 + 40, top + 20, 1.0, f"+{joy} 开心", JOY_COLOR])

    def save_stats(self):
        try:
            with open(STATS_FILE, "w") as f:
                json.dump({"full": self.full, "joy": self.joy, "laps": self.laps,
                           "time": time.time()}, f)
        except OSError:
            pass

    def pick_next(self):
        if self.state == "wheel_in":
            self.set_state("wheel", self.run_len)
            return
        if self.state == "wheel":
            self.set_state("wheel_out", OUT_FRAMES)
            return
        if self.state == "wheel_out":
            self.change(full=-3, joy=4)
            self.say(random.choice(["呼～跑累了", "今天也跑了好远", "锻炼完毕！"]), 70)
        r = random.random()
        if (r < 0.25 and self.full >= 20
                and time.time() - self.last_wheel > WHEEL_MINUTES * 60):
            self.go_wheel(random.randint(180, 360), quiet=random.random() < 0.5)
            return
        if r < 0.85:
            self.set_state("idle", random.randint(80, 200))
        else:
            self.set_state("eat", 150)          # 自己啃点存粮，不加饱腹值
        if self.full < 30 and random.random() < 0.4:
            self.say("肚子饿了……\n喂我瓜子嘛", 90)
        elif self.joy < 30 and random.random() < 0.4:
            self.say("好无聊……\n陪我玩一下嘛", 90)
        elif random.random() < 0.07:
            self.say(random.choice(LINES))

    def pat(self):
        self.set_state("happy", 60)
        self.change(joy=5)
        self.say(random.choice(["吱～！", "好舒服", "再摸一下", "嘿嘿"]), 60)

    def tickle(self):
        self.set_state("tickle", 45)
        self.change(joy=3)
        self.say(random.choice(["好痒！", "别戳肚子啦", "吱吱吱！"]), 55)

    def feed(self):
        if self.full >= 95:
            self.set_state("idle", 60)
            self.say("吃不下啦～\n先存进腮帮子", 80)
            return
        self.set_state("eat", 150)
        self.change(full=15, joy=2)
        self.say("瓜子！咔嚓咔嚓", 90)

    def go_wheel(self, frames=300, quiet=False):
        if self.state in WHEEL_STATES:
            return
        self.run_len = frames
        self.last_wheel = time.time()
        self.set_state("wheel_in", IN_FRAMES)
        if not quiet:
            self.say(random.choice(["跑跑跑！", "冲呀～", "嘿咻嘿咻"]), 60)

    def wheel_progress(self):
        """跑轮出现程度 wk、冬瓜进轮程度 hk、跑步速度 run，都是 0~1"""
        s, f = self.state, 0
        if s == "wheel_in":
            f = IN_FRAMES - self.timer
            return ease_pop(f / 14), ease((f - 14) / 24), 0.0
        if s == "wheel":
            return 1.0, 1.0, min(1.0, (self.run_len - self.timer) / 30)
        if s == "wheel_out":
            f = OUT_FRAMES - self.timer
            return (1 - ease((f - 38) / 14), 1 - ease((f - 14) / 22),
                    max(0.0, 1 - f / 14))
        return 0.0, 0.0, 0.0

    # ---------- 每 10 秒：久坐提醒 + 数值慢慢下降 ----------
    def every_10s(self):
        self.full = clamp(self.full - 10 / 60 / HUNGER_MINUTES)
        self.joy = clamp(self.joy - 10 / 60 / BORED_MINUTES)
        self.save_stats()

        idle = get_idle_seconds()
        if idle is not None and idle >= AWAY_MINUTES * 60:
            self.active_since = time.time()
            if self.state == "remind":
                self.set_state("happy", 50)
                self.say("欢迎回来！", 70)
        elif (time.time() - self.active_since >= SIT_MINUTES * 60
              and self.state != "remind"):
            self.set_state("remind", 10 ** 9)
            self.say(f"坐了 {SIT_MINUTES} 分钟啦\n起来走走吧！\n也让眼睛看看远处～", 10 ** 9)
            notify(f"已经坐了 {SIT_MINUTES} 分钟啦，起来走走吧！也让眼睛看看远处，休息一下～")

    def reset_sitting(self):
        self.active_since = time.time()
        if self.state == "remind":
            self.set_state("happy", 50)
        self.say("好，重新计时～", 70)

    # ---------- 鼠标 ----------
    def pet_rect(self):
        """冬瓜在窗口里占的位置（不算动画偏移）"""
        return QRectF((W - PET_WIDTH) / 2, H - self.pet_h - 8, PET_WIDTH, self.pet_h)

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            g = e.globalPosition()
            self.drag = [g.x() - self.px, g.y() - self.py, g.x(), g.y(), False]

    def mouseMoveEvent(self, e):
        if not self.drag:
            return
        g = e.globalPosition()
        dx, dy, sx, sy, moved = self.drag
        if not moved and abs(g.x() - sx) + abs(g.y() - sy) > 4:
            self.drag[4] = True
            self.was_remind = self.state == "remind"
            self.set_state("held", 10 ** 9)
        if self.drag[4]:
            self.px = g.x() - dx          # 只左右移动，始终坐在 Dock 栏上
            self.move(int(self.px), int(self.py))

    def mouseReleaseEvent(self, e):
        if e.button() != Qt.MouseButton.LeftButton:
            return
        moved = self.drag[4] if self.drag else False
        self.drag = None
        if moved:
            self.set_state("idle", 60)
            self.say(random.choice(["吱！", "放我下来啦", "就坐这儿吧"]), 50)
            if getattr(self, "was_remind", False):
                self.reset_sitting()
        elif self.state == "remind":
            self.reset_sitting()
            self.say("好耶！去走走吧", 80)
        elif self.state in WHEEL_STATES:
            self.change(joy=2)
            self.say(random.choice(["我在跑步！", "给我加油～", "看我多快！"]), 50)
        else:
            r = self.pet_rect()
            if e.position().y() < r.top() + r.height() * 0.5:
                self.pat()
            else:
                self.tickle()

    def contextMenuEvent(self, e):
        menu = QMenu(self)
        menu.setWindowFlags(menu.windowFlags() | Qt.WindowType.FramelessWindowHint
                            | Qt.WindowType.NoDropShadowWindowHint)
        menu.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        menu.setStyleSheet(MENU_STYLE)
        menu.setFont(self.font)
        card = QWidgetAction(menu)
        card.setDefaultWidget(StatsCard(self))
        menu.addAction(card)
        menu.addSeparator()
        menu.addAction("🌻  喂瓜子", self.feed)
        menu.addAction("🤚  摸摸头", self.pat)
        menu.addAction("🎡  去跑轮", self.go_wheel)
        menu.addSeparator()
        menu.addAction("⏱  久坐重新计时", self.reset_sitting)
        menu.addAction("👋  再见", QApplication.quit)
        menu.exec(e.globalPos())

    # ---------- 每一帧 ----------
    def step(self):
        self.t += 1
        if self.state not in ("held", "remind"):
            self.timer -= 1
            if self.timer <= 0:
                self.pick_next()
        if self.text_timer > 0:
            self.text_timer -= 1
        run = self.wheel_progress()[2]
        if run:
            self.wheel_angle += 9 * run
            if self.wheel_angle >= 360:
                self.wheel_angle -= 360
                self.laps += 1

        # 鼠标不在冬瓜身上时让点击穿过窗口，不挡你操作
        over = self.pet_rect().adjusted(-6, -24, 6, 4).contains(
            QPointF(self.mapFromGlobal(QCursor.pos())))
        self.set_click_through(not (over or self.drag))

        r = self.pet_rect()
        if self.state == "happy" and self.t % 6 == 0:
            self.floats.append([W / 2 + random.uniform(-55, 55), r.top() + 10, 1.0,
                                "♥", JOY_COLOR])
        for f in self.floats:
            f[1] -= 1.4
            f[2] -= 0.022
        self.floats = [f for f in self.floats if f[2] > 0]

        self.update()

    # ---------- 画画 ----------
    def wheel_frame(self, p, ground, wk):
        """按出现程度 wk 缩放/淡入跑轮，返回 背板中心x、正面中心x、中心y、横向半径"""
        cx, cy = W / 2, ground - WHEEL_R - 4
        p.setOpacity(max(0.0, min(1.0, wk)))
        k = 0.75 + 0.25 * wk
        p.translate(cx, ground)
        p.scale(k, k)
        p.translate(-cx, -ground)
        rx = WHEEL_R * 0.4                 # 从侧前方看，圆变成竖着的椭圆
        return cx - WHEEL_DEPTH / 2, cx + WHEEL_DEPTH / 2, cy, rx

    def draw_wheel_back(self, p, ground, wk):
        """侧前方看的透明跑轮：背板、计步器、筒状跑道（冬瓜画在它前面）"""
        p.save()
        bx, fx, cy, rx = self.wheel_frame(p, ground, wk)
        R, a = WHEEL_R, self.wheel_angle
        # 底座小脚
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(222, 228, 235, 230))
        for x in (bx, fx):
            p.drawRoundedRect(QRectF(x - 18, ground - 8, 36, 9), 4, 4)
        # 筒身：背板椭圆 + 中间一段 + 开口椭圆，连成一个透明圆筒
        body = QPainterPath()
        body.addEllipse(QPointF(bx, cy), rx, R)
        body.addRect(QRectF(bx, cy - R, fx - bx, 2 * R))
        body.addEllipse(QPointF(fx, cy), rx, R)
        p.setBrush(QColor(238, 242, 247, 95))
        p.drawPath(body.simplified())
        # 背板
        p.setBrush(QColor(244, 247, 250, 130))
        p.setPen(QPen(QColor(200, 208, 217, 230), 2.5))
        p.drawEllipse(QPointF(bx, cy), rx, R)
        # 跑道上的横纹：跟着转，下半圈（冬瓜脚下）更明显
        for i in range(40):
            ang = math.radians(a + i * 9)
            c, sn = math.cos(ang), math.sin(ang)
            p.setPen(QPen(QColor(178, 188, 200, int(50 + 110 * max(0.0, sn))), 1.5))
            y = cy + sn * (R - 3)
            p.drawLine(QPointF(bx + c * (rx - 1), y), QPointF(fx + c * (rx - 1), y))
        # 筒的上下边
        p.setPen(QPen(QColor(205, 212, 220, 220), 2))
        p.drawLine(QPointF(bx, cy - R), QPointF(fx, cy - R))
        p.drawLine(QPointF(bx, cy + R), QPointF(fx, cy + R))
        # 背板中间的白色计步器（跟着背板一起"侧过去"）
        p.save()
        p.translate(bx, cy)
        p.scale(0.62, 1)
        p.setPen(QPen(QColor(214, 218, 224), 2))
        p.setBrush(QColor(252, 252, 252))
        p.drawEllipse(QPointF(0, 0), 27, 27)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(196, 206, 196))
        p.drawRoundedRect(QRectF(-19, -9, 38, 15), 3, 3)
        p.setPen(QColor(60, 66, 60))
        p.setFont(QFont("Consolas" if WINDOWS else "Menlo", 10, QFont.Weight.Bold))
        p.drawText(QRectF(-19, -9, 38, 15), Qt.AlignmentFlag.AlignCenter,
                   f"{self.laps % 10000:04d}")
        p.restore()
        p.restore()

    def draw_wheel_front(self, p, ground, wk):
        """跑轮开口的轮圈和塑料反光（盖在冬瓜前面，看起来像在轮子里）"""
        p.save()
        bx, fx, cy, rx = self.wheel_frame(p, ground, wk)
        R = WHEEL_R
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(QPen(QColor(215, 222, 230, 120), 3.5))     # 透明塑料，能透过它看到冬瓜
        p.drawEllipse(QPointF(fx, cy), rx, R)
        p.setPen(QPen(QColor(255, 255, 255, 90), 1.2))
        p.drawEllipse(QPointF(fx, cy), rx - 3, R - 3)
        # 反光：开口左上一道亮弧 + 筒身一道竖向高光
        p.setPen(QPen(QColor(255, 255, 255, 200), 4, cap=Qt.PenCapStyle.RoundCap))
        p.drawArc(QRectF(fx - rx + 4, cy - R + 4, 2 * rx - 8, 2 * R - 8), 110 * 16, 50 * 16)
        p.setPen(QPen(QColor(255, 255, 255, 90), 6, cap=Qt.PenCapStyle.RoundCap))
        p.drawLine(QPointF(bx + 18, cy - R + 14), QPointF(fx - 6, cy - R + 14))
        p.restore()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHints(QPainter.RenderHint.Antialiasing
                         | QPainter.RenderHint.SmoothPixmapTransform
                         | QPainter.RenderHint.TextAntialiasing)
        s, t = self.state, self.t
        r = self.pet_rect()
        ground = r.bottom()
        head_y = r.top()                 # 气泡放在这条线上面

        # 各种动作：上下跳、左右晃、压扁拉长、歪头
        sx, sy, lift, rot, shift = 1.0, 1.0, 0.0, 0.0, 0.0
        if s == "idle":
            br = math.sin(t * 0.09)
            sy, sx = 1 + br * 0.012, 1 - br * 0.006
            rot = math.sin(t * 0.017) * 2.5
        elif s in ("happy", "remind"):
            k = 0.36 if s == "happy" else 0.26
            ph = abs(math.sin(t * k))
            lift = ph * (20 if s == "happy" else 14)
            sy, sx = (0.92, 1.05) if ph < 0.15 else (1.03, 0.98)
        elif s == "tickle":
            rot = math.sin(t * 1.3) * 8
            shift = math.sin(t * 1.7) * 4
        elif s == "eat":
            nib = math.sin(t * 0.9)
            sy = 1 + nib * 0.01
            lift = max(0, nib) * 1.5
        elif s == "held":
            sy, sx = 1.06, 0.96
            rot = math.sin(t * 0.18) * 7
        elif s in WHEEL_STATES:
            wk, hk, run = self.wheel_progress()
            self.draw_wheel_back(p, ground, wk)
            sx = sy = 1 - 0.38 * hk                       # 跳进轮子时变小一点
            lift = 9 * hk + math.sin(math.pi * hk) * 30   # 跳进去的抛物线
            lift += abs(math.sin(t * 0.8)) * 3 * run      # 跑起来一颠一颠
            rot = math.sin(t * 0.8) * 3 * run
            if wk > 0.5:
                head_y = ground - 2 * WHEEL_R - 4

        # 地上的影子
        if s != "held" and (s not in WHEEL_STATES or self.wheel_progress()[1] < 0.5):
            shadow_w = PET_WIDTH * 0.8 * (1 - lift / 60)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(0, 0, 0, int(55 * (1 - lift / 40))))
            p.drawEllipse(QPointF(W / 2 + shift, ground - 2), shadow_w / 2, 6)

        # 冬瓜本体（以脚底中心为轴做变形）
        p.save()
        p.translate(W / 2 + shift, ground - lift)
        p.rotate(rot)
        p.scale(sx * self.scale, sy * self.scale)
        p.translate(-self.pix.width() / 2, -self.pix.height())
        p.drawPixmap(0, 0, self.pix)

        # 啃瓜子：嘴边一颗瓜子，越啃越小
        if s == "eat":
            size = max(0.45, min(1, self.timer / 90))
            cx, cy = 246, 292
            seed = QPainterPath()
            seed.moveTo(cx, cy - 42 * size)
            seed.quadTo(cx + 30 * size, cy, cx, cy + 40 * size)
            seed.quadTo(cx - 30 * size, cy, cx, cy - 42 * size)
            p.setPen(QPen(QColor(235, 235, 230), 3))
            p.setBrush(QColor(38, 38, 38))
            p.drawPath(seed)
            p.setPen(QPen(QColor(225, 225, 220), 5))
            p.drawLine(QPointF(cx, cy - 28 * size), QPointF(cx, cy + 26 * size))
        p.restore()

        if s in WHEEL_STATES:
            self.draw_wheel_front(p, ground, self.wheel_progress()[0])

        # 飘起来的爱心和加分小字
        for fx, fy, life, text, color in self.floats:
            c = QColor(color)
            c.setAlphaF(max(0.0, min(1.0, life)))
            p.setPen(c)
            if text == "♥":
                p.setFont(QFont("Helvetica", int(12 + (1 - life) * 8)))
            else:
                p.setFont(QFont(self.font.family(), 13, QFont.Weight.Bold))
            p.drawText(QPointF(fx, fy), text)

        # 对话气泡
        if self.text_timer > 0 and self.text:
            p.setFont(self.font)
            fm = QFontMetrics(self.font)
            lines = self.text.split("\n")
            bw = min(W - 10, max(fm.horizontalAdvance(l) for l in lines) + 40)
            bh = fm.height() * len(lines) + 20
            top = max(4, head_y - lift - bh - 26)
            bx = (W - bw) / 2
            # 奶油色圆角气泡 + 粉色描边 + 两个小圆点当尾巴
            p.setPen(QPen(PINK, 2.5))
            p.setBrush(QColor(255, 248, 238, 245))
            p.drawRoundedRect(QRectF(bx, top, bw, bh), 18, 18)
            p.drawEllipse(QPointF(W / 2 - 4, top + bh + 9), 5, 5)
            p.drawEllipse(QPointF(W / 2 + 6, top + bh + 20), 3, 3)
            p.setPen(QColor(236, 150, 150))
            p.setFont(QFont("Helvetica", 11))
            p.drawText(QPointF(bx + bw - 16, top + 15), "♥")
            p.setFont(self.font)
            p.setPen(BROWN)
            p.drawText(QRectF(bx, top, bw, bh), Qt.AlignmentFlag.AlignCenter, self.text)
        p.end()


def objc_send(obj, selector, arg=None, argtype=None):
    """调用 Mac 系统自带的功能；不是 Mac 或调用失败时返回 None"""
    try:
        lib = ctypes.cdll.LoadLibrary(ctypes.util.find_library("objc"))
        lib.objc_getClass.restype = ctypes.c_void_p
        lib.sel_registerName.restype = ctypes.c_void_p
        send = lib.objc_msgSend
        send.restype = ctypes.c_void_p
        send.argtypes = [ctypes.c_void_p, ctypes.c_void_p] + ([argtype] if argtype else [])
        if isinstance(obj, str):
            obj = lib.objc_getClass(obj.encode())
        return send(obj, lib.sel_registerName(selector.encode()),
                    *([arg] if argtype else []))
    except Exception:
        return None


def hide_dock_icon():
    """不在 Dock 栏显示 Python 火箭图标"""
    objc_send(objc_send("NSApplication", "sharedApplication"),
              "setActivationPolicy:", 1, ctypes.c_long)


if __name__ == "__main__":
    app = QApplication(sys.argv)
    hide_dock_icon()
    pet = Pet()
    app.aboutToQuit.connect(pet.save_stats)
    pet.show()
    sys.exit(app.exec())
