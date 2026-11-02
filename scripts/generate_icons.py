"""Create original desktop application icons from code. GPL-2.0-only."""
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
DESTINATION = ROOT / 'desktop' / 'assets'


def font(size):
    candidates = ('C:/Windows/Fonts/seguisb.ttf',
                  '/System/Library/Fonts/Supplemental/Arial Bold.ttf',
                  '/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf')
    return next((ImageFont.truetype(p, size) for p in candidates if Path(p).is_file()),
                ImageFont.load_default(size=size))


def main():
    DESTINATION.mkdir(parents=True, exist_ok=True)
    image = Image.new('RGBA', (1024, 1024), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((16, 16, 1008, 1008), 205, fill='#151b22')
    draw.rounded_rectangle((76, 76, 948, 948), 158, outline='#dfbb77', width=12)
    draw.rounded_rectangle((133, 234, 891, 719), 35, fill='#202a34', outline='#dfbb77', width=10)
    for x in (170, 290, 410, 530, 650, 770):
        draw.rounded_rectangle((x, 187, x + 76, 213), 8, fill='#dfbb77')
        draw.rounded_rectangle((x, 739, x + 76, 765), 8, fill='#dfbb77')
    draw.text((512, 455), '8K', font=font(340), anchor='mm', fill='#f5ddb0', stroke_width=1)
    draw.text((512, 842), 'LOSSLESS', font=font(70), anchor='mm', fill='#d8e0e9')
    image.save(DESTINATION / 'icon.png', optimize=True)
    image.save(DESTINATION / 'icon.ico', sizes=[(s, s) for s in (16, 24, 32, 48, 64, 128, 256)])
    image.save(DESTINATION / 'icon.icns', format='ICNS')
    print(DESTINATION)


if __name__ == '__main__':
    main()
