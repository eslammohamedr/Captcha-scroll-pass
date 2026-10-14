# Captcha Scroll Pass

An automated tool powered by Python, Playwright, and OpenCV designed to solve **Dingxiang sliding puzzle CAPTCHAs locally** (zero third-party API costs) and perform bulk device warranty and activation verifications against official support portals.

---

## 🌟 Key Features

- **100% Local CAPTCHA Solving:** No third-party CAPTCHA solving APIs (like 2Captcha, CapSolver, or Anti-Captcha) required.
- **Dual-Strategy Computer Vision:**
  - **Primary:** **CIE LAB ($L^*$ Channel) Edge Template Matching** – decouples luminance from chromatic channels, eliminating contrast loss on purple, dark-blue, and multi-colored backgrounds.
  - **Secondary:** **CLAHE Dark-Shadow Contour Detection** – automatically activates if template matching correlation is low.
- **Closed-Loop DOM Alignment:** Continuously samples the live DOM displacement of the slider piece (`sub-slider`) during the mouse drag, releasing at the exact target coordinate with sub-pixel precision. Eliminates drift caused by non-linear gear ratios, browser zoom, and display DPI scaling (125%, 150%).
- **Natural Human Movement Trajectories:** Drag paths follow an eased velocity curve with subtle sinusoidal micro-variations to prevent bot-detection flags.
- **Automated Excel Reporting:** Exports all verification details into a formatted spreadsheet (`oppo_results.xlsx`) with status color-coding, auto-fitted columns, and incremental saving.

---

## 📦 Requirements & Libraries

- **Python 3.10+**
- **Libraries (`requirements.txt`):**
  - `playwright` – Browser automation and DOM inspection
  - `opencv-python` – Computer vision image processing
  - `numpy` – Numerical array computations
  - `openpyxl` – Formatted Excel spreadsheet generation
  - `pandas` – Fallback tabular data handling
  - `tqdm` – Live console progress bar
  - `requests` – Direct HTTP asset fetching

---

## 🚀 Installation & Setup

### 1. Clone the Repository
```bash
git clone https://github.com/moham/Captcha-scroll-pass.git
cd Captcha-scroll-pass
```

### 2. (Optional) Create a Virtual Environment
```bash
python -m venv .venv
# On Windows (PowerShell):
.venv\Scripts\Activate.ps1
# On Linux/macOS:
source .venv/bin/activate
```

### 3. Install Dependencies
```bash
pip install -r requirements.txt
```

### 4. Install Playwright Chromium Browser
```bash
playwright install chromium
```

---

## 📝 Preparing Input Data

Create or edit `serials.txt` in the root folder. Add one IMEI or Serial Number per line:
```text
863546086797691
863546086794854
863546086746730
863546086745534
```

Lines starting with `#` or empty lines are automatically ignored.

---

## 💻 How to Run

### 1. Run in Visible Browser Mode (Recommended for Testing)
Watch the browser automatically load serials, solve the slider CAPTCHA, and extract details:
```bash
python check_oppo_cv.py --skip-found --headful
```

### 2. Run in Headless Mode (Fastest)
Runs silently in the background:
```bash
python check_oppo_cv.py --skip-found
```

### 3. Process a Limited Batch
To check only the first $N$ unverified devices:
```bash
python check_oppo_cv.py --skip-found --limit 10
```

### 4. Command-Line Options

| Argument | Type | Default | Description |
| :--- | :---: | :---: | :--- |
| `--input` | `Path` | `serials.txt` | Path to text or excel file containing serials |
| `--output` | `Path` | `oppo_results.xlsx` | Path to generated Excel report |
| `--headful` | `Flag` | `False` | Run with visible browser window |
| `--skip-found` | `Flag` | `False` | Skip serials already marked as `Found` in output |
| `--limit` | `int` | `None` | Process only the first $N$ devices |
| `--delay` | `float` | `2.0` | Delay in seconds between consecutive serial checks |
| `--adjust` | `float` | `0.0` | Manual pixel nudge (+ moves right, - moves left) |

---

## 📊 Expected Output & Data Format

The script writes results in real time to `oppo_results.xlsx`. If interrupted, verified rows remain saved.

### Columns in `oppo_results.xlsx`:

1. **Serial / IMEI:** The queried device identifier.
2. **Active?:** `Yes` (Green), `No` (Yellow), `Pending` (Blue), or `Not Found` (Orange).
3. **Activation Date:** Official date of first activation, or `Not Activated`.
4. **Activation Comment:** The exact status message shown on the portal:
   - `Not Activated` – Phone has never been connected or registered.
   - `The activation time has been transferred to the system. Please check again after 7 days.` – Recently activated device currently synchronizing.
   - Exact timestamp (e.g. `2024-05-12 14:32:10`) – Fully registered device.
5. **Model Name:** Marketing product name (e.g., `OPPO A6`).
6. **RAM/ROM:** Memory configuration (e.g., `8GB+256GB`).
7. **Color:** Device finish (e.g., `Aurora Gold`).
8. **Certified Model:** Regulatory code (e.g., `CPH2817`).
9. **Region:** Destination market country code (e.g., `UA`).
10. **Warranty End:** Official warranty expiration date.
11. **Check Status:** `Found`, `Not Found`, or `Captcha Failed`.
12. **Last Checked:** Timestamp of verification.

---

## 🖥️ Windows Desktop GUI (For Customers & Easy Use)

A user-friendly graphical interface is included so non-technical users and customers can run verifications without using the command line:

![OPPO Verification GUI](https://raw.githubusercontent.com/eslammohamedr/Captcha-scroll-pass/main/gui_preview.png)

### Features of the GUI:
- **One-Click Launch:** Simply double-click `run_gui.bat` or `OPPO_Checker.bat`.
- **Flexible Input:** Choose a file (`.txt`, `.xlsx`, `.csv`) or paste serials directly into the multiline text box.
- **Full Automation Controls:**
  - Toggle visible browser (Headful) or background (Headless).
  - Skip already verified records in Excel.
  - Configure delay, batch limits, and manual slider nudge offsets.
- **Live Monitoring:**
  - Real-time stat summary cards (Total, Processed, Activated, Not Activated, Pending, Errors).
  - Smooth progress bar (0% - 100%) with status updates.
  - Interactive live results table with color coding. Double-click any row to view full details.
  - Integrated activity log console with auto-scroll.
- **One-Click Excel Viewer:** Open `oppo_results.xlsx` instantly with the "📊 Open Excel" button.
- **Automated Browser Setup:** "⚙ Setup Browser" button to download and configure Playwright Chromium in one click.

### How to Run the GUI:
```cmd
# Double click the batch file:
run_gui.bat

# Or run directly via Python:
python gui_app.py
```

### Sending to Customers:
1. **Option A (Simplest - Portable Folder):**
   - Zip the project folder and send it to your customer.
   - The customer only needs Python installed (with "Add Python to PATH" checked).
   - When they double-click `run_gui.bat` or `OPPO_Checker.bat`, it will automatically verify dependencies, install Playwright Chromium, and open the app.
2. **Option B (Standalone .EXE via PyInstaller):**
   - Install PyInstaller: `pip install pyinstaller`
   - Build single executable:
     ```cmd
     pyinstaller --noconsole --onefile --name "OPPO_Checker" gui_app.py
     ```

---

## 🛠 Standalone Computer Vision Solver (`slider_cv.py`)

You can test the computer vision engine directly on saved CAPTCHA images:

```bash
# Match background and piece images
python slider_cv.py --bg background.png --piece piece.png

# Detect cutout hole purely by shadow contour
python slider_cv.py --bg background.png --output detected_hole.png
```

---

## ⚠️ Troubleshooting & Tips

- **PermissionError: [WinError 32]:**
  - **Cause:** `oppo_results.xlsx` is currently open in Microsoft Excel on Windows, which locks the file against writing.
  - **Solution:** Close Excel or click "Retry". The GUI automatically pauses and retries saving so data is never lost.
- **Connection Latency:**
  - If your internet connection is slow, increase the delay using `--delay 3.0` or `--delay 4.0` (or adjust the delay slider in the GUI).
