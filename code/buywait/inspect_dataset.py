from __future__ import annotations

from .loaders import load_dataset


def main() -> None:
    dataset = load_dataset()
    for name, count in dataset.counts().items():
        print(f"{name}: {count}")


if __name__ == "__main__":
    main()
