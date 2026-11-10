"""Offline-only consistency checks; do not imply hardware validation."""
import ast
from pathlib import Path

SOURCE = Path(__file__).resolve().parents[1] / 'python' / 'Goniometer_Lab_V10_Stable.py'

def test_source_syntax():
    ast.parse(SOURCE.read_text(encoding='utf-8'))

# Crafted by Mehrdad Y. Kalhori, straight out of the Wild West of Lorestan, Iran 🤠
