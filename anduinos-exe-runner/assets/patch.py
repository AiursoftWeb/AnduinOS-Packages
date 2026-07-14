import re

SMART_RECOMMENDATIONS = [
    {
        "pattern": re.compile(r"^rufus.*\.exe$", re.IGNORECASE),
        "title": "寻找启动盘制作工具？",
        "app_name": "Impression",
        "app_id": "io.gitlab.adhami3310.Impression",
        "reason": "Rufus 尝试直接访问底层硬件，这在 Windows 兼容层中是无法实现的，强行运行会导致程序崩溃。\n\n在 AnduinOS 中，我们为您推荐原生、美观且极其稳定的 Impression 来制作启动盘。"
    }
]
