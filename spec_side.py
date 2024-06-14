import numpy as np
from PIL import Image, ImageDraw, ImageFont
import logging
import ST7789
import LCD_side
import time
from picamera2 import Picamera2
from gpiozero import Button
from flask import Flask, send_file, render_template_string
import threading
import io
import spidev as SPI
from scipy.signal import find_peaks
import datetime

# Set up logging
logging.basicConfig(level=logging.DEBUG)

# Initialize the main display
disp_main = ST7789.ST7789(spi=SPI.SpiDev(1, 0), spi_freq=10000000, rst=27, dc=22, bl=19)
disp_main.Init()
disp_main.clear()
disp_main.bl_DutyCycle(100)
disp_main.bl_Frequency(1000)

# Initialize the side display
disp_side = LCD_side.LCD_0inch96(spi=SPI.SpiDev(0, 1), spi_freq=10000000, rst=23, dc=5, bl=12)
disp_side.Init()
disp_side.clear()
disp_side.bl_DutyCycle(100)
disp_side.bl_Frequency(1000)

# GPIO Pin Definitions
KEY1_PIN = 25
KEY2_PIN = 26
KEY3_PIN = 16

# Initialize buttons
button1 = Button(KEY1_PIN)
button2 = Button(KEY2_PIN)

# Variables to control the reference spectra
reference_spectra = None
current_plot = Image.new('RGB', (240, 240), 'white')  # Initialize current_plot
current_camera_image = Image.new('RGB', (240, 240), 'black')  # Initialize current_camera_image

def capture_full_res_image():
    timestamp = datetime.now().isoformat()
    global picam2
    picam2.stop()
    config = picam2.create_still_configuration(main={"size": (1920, 1080)})  # Full resolution
    picam2.configure(config)
    picam2.start()
    picam2.capture_file(f"full_res_{timestamp}.jpg")
    picam2.stop()

    # Reconfigure the camera for the regular preview mode
    config = picam2.create_still_configuration(main={"size": (240, 240)})
    picam2.configure(config)
    picam2.start()

    # Process the full-resolution image
    full_res_image = Image.open(f"full_res_{timestamp}.jpg")
    spectra, light_color = process_frame(np.array(full_res_image))
    spectra_img = plot_spectra(spectra, light_color, reference_spectra, width=640, height=480)  # Larger plot size
    spectra_img.save(f"full_res_plot_{timestamp}.png")

    logging.info("Full-resolution photo and plot captured")

def capture_reference_spectra():
    global reference_spectra
    global picam2
    frame = picam2.capture_array()
    reference_spectra, _ = process_frame(frame)
    logging.info("Reference spectra captured")

button1.when_pressed = capture_full_res_image
button2.when_pressed = capture_reference_spectra

# Flask setup
app = Flask(__name__)

@app.route('/')
def index():
    return render_template_string("""
    <!doctype html>
    <title>Spectra Plot and Camera View</title>
    <h1>Spectra Plot</h1>
    <img id="plot" src="/plot.png" alt="Spectra Plot">
    <h1>Camera View</h1>
    <img id="camera" src="/camera.png" alt="Camera View">
    <br>
    <a href="/fullres">Capture Full-Resolution Image</a>
    <script>
        function refreshImage(id, url) {
            document.getElementById(id).src = url + '?t=' + new Date().getTime();
        }
        setInterval(() => {
            refreshImage('plot', '/plot.png');
            refreshImage('camera', '/camera.png');
        }, 1000);
    </script>
    """)

@app.route('/fullres')
def fullres():
    return render_template_string("""
    <!doctype html>
    <title>Full Resolution Image</title>
    <h1>Full Resolution Image</h1>
    <img id="fullres" src="/fullres_image.png" alt="Full Resolution Image">
    <h1>Camera View</h1>
    <img id="camera" src="/camera.png" alt="Camera View">
    <br>
    <a href="/">Back to Main Page</a>
    <script>
        function refreshImage(id, url) {
            document.getElementById(id).src = url + '?t=' + new Date().getTime();
        }
        setInterval(() => {
            refreshImage('camera', '/camera.png');
        }, 1000);
    </script>
    """)

@app.route('/plot.png')
def plot_png():
    global current_plot
    img_io = io.BytesIO()
    current_plot.save(img_io, 'PNG')
    img_io.seek(0)
    return send_file(img_io, mimetype='image/png')

@app.route('/camera.png')
def camera_png():
    global current_camera_image
    img_io = io.BytesIO()
    current_camera_image.save(img_io, 'PNG')
    img_io.seek(0)
    return send_file(img_io, mimetype='image/png')

@app.route('/fullres_image.png')
def capture_full_res_image_route():
    capture_full_res_image()
    img_io = io.BytesIO()
    full_res_image = Image.open("full_res.jpg")
    full_res_image.save(img_io, 'PNG')
    img_io.seek(0)
    return send_file(img_io, mimetype='image/png')

def start_flask():
    app.run(host='0.0.0.0', port=5000)

# Function to process the image and extract the spectra
def process_frame(frame):
    # Sum the pixel values along the horizontal axis to get the combined spectra
    spectra = np.sum(frame, axis=1)
    light_color = np.max(frame, axis=1)
    return spectra, light_color

# Function to find peaks in the spectra
def find_peaks_in_spectra(spectra, distance=10, height=None):
    peaks, _ = find_peaks(spectra, distance=distance, height=height)
    return peaks

# Function to plot the spectra
def plot_spectra(spectra, light_color, reference_spectra=None, width=240, height=240):
    spectra_img = Image.new('RGB', (width, height), 'white')
    draw = ImageDraw.Draw(spectra_img)

    # Normalize the spectra to fit the height of the image
    combined_spectra = np.sum(spectra, axis=1)  # Sum across all three channels
    max_intensity = np.max(combined_spectra)
    normalized_spectra = (combined_spectra / max_intensity * (height - 1)).astype(int)

    for x, intensity in enumerate(normalized_spectra):
        r, g, b = light_color[x]
        draw.line([(x, 0), (x, intensity)], fill=(r, g, b))  # Vertical bar

    # If reference spectra is provided, plot the transmission
    if reference_spectra is not None:
        combined_reference_spectra = np.sum(reference_spectra, axis=1)
        with np.errstate(divide='ignore', invalid='ignore'):
            transmission = np.where(combined_reference_spectra > 0, (combined_spectra / combined_reference_spectra) * 100, 0)
        max_transmission = np.max(transmission[np.isfinite(transmission)])
        if max_transmission > 0:
            normalized_transmission = (transmission / max_transmission * (height - 1)).astype(int)
            for x, intensity in enumerate(normalized_transmission):
                if np.isfinite(intensity) and intensity >= 0:
                    draw.line([(x, 0), (x, intensity)], fill='blue')
    return spectra_img

# Function to display image on LCD
def display_on_lcd(image, disp):
    img = image.resize((disp.width, disp.height))
    disp.ShowImage(img)

# Function to display the wavelengths of the peaks
def display_peaks(peaks, wavelengths, disp):
    peaks_img = Image.new('RGB', (disp.width, disp.height), 'white')
    draw = ImageDraw.Draw(peaks_img)
    font = ImageFont.load_default()

    for i, (peak, wavelength) in enumerate(zip(peaks, wavelengths)):
        draw.text((5, i * 10), f"Peak {i + 1}: {wavelength} nm", font=font, fill="black")

    display_on_lcd(peaks_img, disp)

# Main function
def main():
    global reference_spectra
    global picam2
    global current_plot
    global current_camera_image
    picam2 = Picamera2()
    config = picam2.create_still_configuration(main={"size": (160,160)})  # Use full display height for the camera
    picam2.configure(config)
    picam2.start()

    # Start Flask in a separate thread
    flask_thread = threading.Thread(target=start_flask)
    flask_thread.daemon = True
    flask_thread.start()

    while True:
        try:
            start = time.time()
            frame = picam2.capture_array()
            camera_img = Image.fromarray(frame)
            current_camera_image = camera_img  # Save the current camera image to be served by Flask

            # Display camera image on main display
            display_on_lcd(camera_img, disp_main)
            
            # Process frame and plot spectra
            spectra, light_color = process_frame(frame)
            spectra_img = plot_spectra(spectra, light_color, reference_spectra, width=240, height=240)
            current_plot = spectra_img  # Save the current plot to be served by Flask
            display_on_lcd(spectra_img, disp_side)

            # Find peaks in the spectra
            peaks = find_peaks_in_spectra(np.sum(spectra, axis=1), distance=10)
            wavelengths = peaks * (240 / len(spectra))  # Simplified wavelength calculation
            display_peaks(peaks[:10], wavelengths[:10], disp_side)  # Display up to 10 peaks

            logging.info(f'Frame processing time: {time.time() - start}')
            time.sleep(0.1)  # Short delay between frames

        except KeyboardInterrupt:
            logging.info("Exiting the loop.")
            break

    picam2.stop()

if __name__ == '__main__':
    main()
