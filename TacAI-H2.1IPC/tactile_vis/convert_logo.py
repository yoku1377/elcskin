"""
Convert logo.svg to logo.ico

Dependencies:
    uv pip install pillow cairosvg

Author: Yijie Luo
Email: yijie_luo@tongji.edu.cn
"""

import os
import sys

try:
    from PIL import Image
except ImportError:
    print("Pillow required. Run: uv pip install pillow")
    sys.exit(1)

try:
    import cairosvg
except ImportError:
    print("cairosvg required. Run: uv pip install cairosvg")
    print("")
    print("Or use an online converter:")
    print("  - https://convertio.co/svg-ico/")
    print("  - https://www.icoconverter.com/")
    sys.exit(1)


def svg_to_ico(svg_path, ico_path, sizes=[16, 32, 48, 64, 128, 256]):
    """Convert SVG to multi-size ICO file"""
    if not os.path.exists(svg_path):
        print(f"Error: {svg_path} not found")
        return False

    print(f"Converting: {svg_path} -> {ico_path}")

    max_size = max(sizes)
    png_temp = "temp_logo.png"

    cairosvg.svg2png(
        url=svg_path,
        write_to=png_temp,
        output_width=max_size,
        output_height=max_size
    )

    img = Image.open(png_temp)

    images = []
    for size in sizes:
        if size <= max_size:
            resized = img.resize((size, size), Image.Resampling.LANCZOS)
            images.append(resized)

    images[0].save(
        ico_path,
        format='ICO',
        sizes=[(img.size[0], img.size[1]) for img in images],
        quality=100
    )

    os.remove(png_temp)

    print(f"Success: {ico_path}")
    return True


if __name__ == "__main__":
    script_dir = os.path.dirname(os.path.abspath(__file__))
    svg_file = os.path.join(script_dir, "logo.svg")
    ico_file = os.path.join(script_dir, "logo.ico")

    if svg_to_ico(svg_file, ico_file):
        print("\nNow run build.bat to package the application!")