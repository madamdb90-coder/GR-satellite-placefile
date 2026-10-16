GR SATELLITE PLACEFILE - THE VERSION THAT WORKS
===============================================

Draws GOES satellite (GeoColor, Visible, Infrared) on your GR2Analyst / GRLevel3
map, centered on the radar you are viewing, as a loop of frames that follows GR's
playback. This zip is the same program that is running on the PC it was made for.
The box is 600 x 600 miles.

IN THIS ZIP
  sat_placefile.py      the program
  start_satellite.bat   double-click launcher (runs it with the 600 mile box)
  README.txt            this file

SETUP (one time)
1. Install Python from https://www.python.org/downloads/
   In the installer, tick "Add python.exe to PATH".
2. Right-click the zip -> Extract All. Open the extracted folder. (If it looks empty,
   open the folder inside it. Windows sometimes adds an extra level.)

EACH TIME
1. Double-click start_satellite.bat. If Windows warns about it, click "More info",
   then "Run anyway". A black window opens. LEAVE IT OPEN.
   (No double-click? Open a Command Prompt in this folder and type:
        py sat_placefile.py --half-deg 4.35 )
2. In GR: Windows -> Show Placefile Manager. Click the + button and add:
     http://localhost:8000/geocolor.txt
     http://localhost:8000/visible.txt
     http://localhost:8000/ir.txt
   You only add them once; GR remembers them.
3. Tick ONE of the three satellite rows. Frames download in the background, so give
   it a minute or two. Open http://localhost:8000/ir.txt in a browser and press F5:
   the "frame(s) ready" line should count up.

TO STOP: press Ctrl+C in the black window, or close it.

BOX SIZE
The box is 2 x 69 miles x (half-deg). 4.35 gives 600 miles. For another size,
edit start_satellite.bat and change 4.35 (miles / 138 = the number to use).

NOTES
- Visible is dark at night. Use Infrared or GeoColor in the evening.
- Imagery updates every 10 minutes and runs about 40 minutes behind real time.
- GR names each row from the placefile, so the rows are called "Satellite loop - ...".
- If the satellite hides your radar, try the second checkbox next to the placefile.
- Replay a past event (last ~90 days), times in UTC, from a terminal in this folder:
     py sat_placefile.py --half-deg 4.35 --start 2026-10-15T22:00Z --end 2026-10-16T02:00Z
- All options: py sat_placefile.py --help

IF SOMETHING GOES WRONG
- "Address already in use": it is already running in another black window. Close it.
- Rows stay red in GR: the black window is closed or printed an error.
- "can't open file": you are in the wrong folder. Type dir and look for sat_placefile.py.
- Do not mix files from different downloads. Use only the ones in this zip.

CREDITS
Imagery: NOAA GOES-East via NASA GIBS. GeoColor was developed at CIRA/RAMMB;
please credit CIRA/NOAA if you share images made with it.
Not affiliated with Gibson Ridge Software.
