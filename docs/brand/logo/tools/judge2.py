import os, sys
sys.path.insert(0, "/path/to/xsai-workbench/logo-gen")
from see import ask
Q = """你是资深品牌设计总监。评审这张 logo 概念图，用于产品"星采"（自媒体创作的自动化素材管理加工工具，面向中文创作者）。
要求：简洁、几何、有力量感、缩到 16px 仍辨识、不花哨、不能像通用图标库的现成符号。

严格按格式，每项一句话：
1. 实际画的是什么：（客观描述）
2. 辨识度（16px）：强/中/弱 + 理由
3. 简洁度：好/一般/差 + 理由
4. 像不像现成模板或通用图标：是/否 + 理由
5. 小瑕疵：具体指出
6. 总分(1-10)：只给数字
7. 一句话结论：值不值得深化
"""
d = sys.argv[1]
for f in sorted(os.listdir(d)):
    if not f.endswith(".png"): continue
    print(f"\n{'='*58}\n{f}\n{'='*58}")
    print(ask(os.path.join(d, f), Q, max_side=700))
