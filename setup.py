from setuptools import setup, find_packages

with open("README.md", "r", encoding="utf-8") as fh:
    long_description = fh.read()

setup(
    name="crec",
    version="1.0.1",
    author="knkr1",
    author_email="kaan@karesi.dev",
    description="The Ultimate Media Downloader - Download videos, audio, and images from various platforms",
    long_description=long_description,
    long_description_content_type="text/markdown",
    url="https://github.com/knkr1/crec",
    packages=find_packages(include=['crec', 'crec.*']),
    include_package_data=True,
    package_data={
        'crec': ['py.typed'],
    },
    classifiers=[
        "Development Status :: 4 - Beta",
        "Intended Audience :: End Users/Desktop",
        "Topic :: Multimedia :: Video",
        "Topic :: Multimedia :: Sound/Audio",
        "License :: OSI Approved :: MIT License",
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: 3.7",
        "Programming Language :: Python :: 3.8",
        "Programming Language :: Python :: 3.9",
        "Programming Language :: Python :: 3.10",
        "Programming Language :: Python :: 3.11",
    ],
    python_requires=">=3.7",
    install_requires=[
        "yt-dlp>=2023.12.30",
        "ffmpeg-python>=0.2.0",
        "win10toast>=0.9; platform_system == 'Windows'",
    ],
    entry_points={
        "console_scripts": [
            "crec=crec.cli:main",
        ],
    },
) 