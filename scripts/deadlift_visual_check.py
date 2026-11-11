"""只运行已固定协议的硬拉视觉开发验证；不提供独立测试或产品实验入口。"""
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from fitness_app.paths import configure_environment
configure_environment()
from fitness_app.deadlift_visual import development

if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser();parser.add_argument('--features-only',action='store_true');parser.add_argument('--reuse-only',action='store_true');parser.add_argument('--masked',action='store_true');args=parser.parse_args()
    if args.masked:from fitness_app.deadlift_visual_mask import development
    development(features_only=args.features_only,reuse_only=args.reuse_only)
