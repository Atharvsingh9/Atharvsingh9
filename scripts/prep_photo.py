from __future__ import annotations

import argparse
from pathlib import Path

import cv2
import numpy as np
from PIL import Image
from rembg import remove


def remove_background(image: Image.Image) -> Image.Image:
    """Remove the image background while preserving transparency."""
    return remove(image)


def composite_on_white(image: Image.Image) -> Image.Image:
    """Place transparent image onto a white background."""
    image = image.convert("RGBA")

    white = Image.new("RGBA", image.size, (255, 255, 255, 255))
    white.alpha_composite(image)

    return white.convert("RGB")


def enhance_contrast(image: Image.Image) -> Image.Image:
    """
    Enhance local contrast using CLAHE.
    This makes facial features much clearer for ASCII conversion.
    """

    gray = cv2.cvtColor(np.array(image), cv2.COLOR_RGB2GRAY)

    clahe = cv2.createCLAHE(
        clipLimit=2.5,
        tileGridSize=(8, 8),
    )

    enhanced = clahe.apply(gray)

    return Image.fromarray(enhanced)


def prepare_photo(input_path: Path, output_path: Path) -> None:
    if not input_path.exists():
        raise FileNotFoundError(f"{input_path} not found")

    image = Image.open(input_path).convert("RGB")

    image = remove_background(image)

    image = composite_on_white(image)

    image = enhance_contrast(image)

    output_path.parent.mkdir(parents=True, exist_ok=True)

    image.save(output_path)

    print(f"✓ Saved {output_path}")


def main():
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "image",
        type=Path,
        help="Input photo",
    )

    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        default=Path("assets/source-prepped.png"),
    )

    args = parser.parse_args()

    prepare_photo(args.image, args.output)


if __name__ == "__main__":
    main()