# The Forge on Windows, by hand

The PowerShell wizard shows this screen for the Forge role: "In this release deep32-setup.ps1 cannot install the
Windows Forge: it starts the installer without options it requires. Run these instead:"

1. "In PowerShell, not as administrator, in this folder, download disc 3 for this card:"

   ```powershell
   powershell -NoProfile -ExecutionPolicy Bypass -File src\release-tools\deep32-get.ps1 disc3 --gpu-class CLASS --ingestion --to .\deep32-downloads
   ```

2. "In PowerShell as administrator (Start, type PowerShell, right-click, Run as administrator), install from the folder
   that holds the verified images and the signed release files:"

   ```powershell
   powershell -NoProfile -ExecutionPolicy Bypass -File src\release-tools\deep32-get.ps1 disc3 --gpu-class CLASS --journey J1 --install --data-root C:\Deep32 --from FOLDER --confirm-tailnet YOUR-TAILNET.ts.net --confirm-desktop-isolation --docker-storage-path DIR
   ```

3. "FOLDER is where step 1 put the images, YOUR-TAILNET your tailnet name and DIR the folder of Docker Desktop's disk
   image. The installer names any other option it needs; this wizard's error notes explain its messages."

The wizard saves the same steps as `forge-by-hand.txt` in its log folder. Fill in the values with the person:

| Value | What it is |
|---|---|
| CLASS | The card's class from its compute capability: 7.5 `turing`, 8.0 `ampere80`, 8.6 or 8.7 `ampere86`, 8.9 `ada`, 9.0 `hopper`, 10.0 `blackwell100`, 12.0 `blackwell120` (INSTALL.md section 4). The wizard shows the class it found. |
| FOLDER | `.\deep32-downloads` after step 1: the three verified images with the signed release files. |
| YOUR-TAILNET | The tailnet name, ending in `.ts.net`, from the Tailscale admin console's DNS page. |
| DIR | Docker Desktop's disk image location: Docker Desktop, Settings, Resources, Advanced. |
| `--confirm-desktop-isolation` | Add it only after the person has checked that Docker Desktop's WSL integration is off for unrelated distributions. |

Step 2 runs in the person's administrator window and asks there for the non-secret settings it lacks, for agreement
to Certificate Transparency ("Allow the Forge hostname to appear in Certificate Transparency logs? [y/N]") and for the
plan digest ("Review this target and settings. Type the plan digest to install"). All of these are the person's
answers. Then the pairing helper opens the five-minute window, from an elevated PowerShell in the clone; the current
code is in `C:\Deep32\status\windows-pair`:

```powershell
python -I -B src\release-tools\forge_pair_windows.py --data-root C:\Deep32
```
