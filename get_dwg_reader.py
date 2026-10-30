"""One-time download of the portable DWG reader (LibreDWG, free / GPL) into tools/libredwg.
Double-click, or open in IDLE / VS Code and Run (F5). Needs internet once.
If the office network blocks it: download the zip below in your browser and unzip it
into  tools/libredwg  next to Route_Importer.pyw yourself."""
import io
import os
import urllib.request
import zipfile

URL = "https://github.com/LibreDWG/libredwg/releases/download/0.13.3/libredwg-0.13.3-win64.zip"
HERE = os.path.dirname(os.path.abspath(__file__))
DEST = os.path.join(HERE, "tools", "libredwg")

if __name__ == "__main__":
    try:
        print("Downloading", URL)
        data = urllib.request.urlopen(URL, timeout=120).read()
        os.makedirs(DEST, exist_ok=True)
        zipfile.ZipFile(io.BytesIO(data)).extractall(DEST)
        ok = os.path.isfile(os.path.join(DEST, "dwgread.exe"))
        print("Done -", "dwgread.exe ready in tools/libredwg" if ok else "dwgread.exe not found in the zip")
    except Exception as e:
        print("Download failed:", e)
        print("Download the zip in your browser:\n ", URL, "\nand unzip it into", DEST)
    finally:
        input("\nPress Enter to close")
