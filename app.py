from flask import Flask, Response, render_template, request, jsonify, send_file
from PIL import Image
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import letter
from PyPDF2 import PdfMerger, PdfReader, PdfWriter
from reportlab.lib.utils import ImageReader
import io
import os
import logging

app = Flask(__name__)

logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)

ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg'}
MIN_SIZE = (420, 540)
TARGET_FORMAT = 'JPEG'
TARGET_COLOR_MODE = 'RGB'
MAX_FILE_SIZE = 4 * 1024 * 1024  # 4 MB


def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS


def process_image(image, quality=85):
    # Convert to RGB if not already
    if image.mode != TARGET_COLOR_MODE:
        image = image.convert(TARGET_COLOR_MODE)

    # Calculate scaling factor if image is smaller than MIN_SIZE
    # width, height = image.size
    # scale_w, scale_h = MIN_SIZE[0] / width, MIN_SIZE[1] / height
    # Use 1 if image is already larger than MIN_SIZE
    # scale = max(scale_w, scale_h, 1)

    # Resize image only if it's smaller than MIN_SIZE
    # if scale > 1:
    #     new_size = (int(width * scale), int(height * scale))
    #     image = image.resize(new_size, Image.LANCZOS)

     # Resize image if it's smaller than MIN_SIZE
    if image.size[0] < MIN_SIZE[0] or image.size[1] < MIN_SIZE[1]:
        image.thumbnail(MIN_SIZE, Image.LANCZOS)

    # Save as JPEG
    buffer = io.BytesIO()
    image.save(buffer, format=TARGET_FORMAT, quality=quality)
    buffer.seek(0)

    return buffer


def validate_image(image_buffer):
    image_buffer.seek(0)
    size = image_buffer.getbuffer().nbytes
    image = Image.open(image_buffer)
    width, height = image.size

    criteria = {
        "size": size <= MAX_FILE_SIZE,
        "dimensions": width >= MIN_SIZE[0] and height >= MIN_SIZE[1],
        "format": image.format == TARGET_FORMAT,
        "color_mode": image.mode == TARGET_COLOR_MODE
    }

    return criteria


def create_pdf(images):
    pdf_buffer = io.BytesIO()
    c = canvas.Canvas(pdf_buffer, pagesize=letter)

    for img in images:
        img_width, img_height = img.size
        # Calculate scaling factor to fit image on page
        width_ratio = (letter[0] - 40) / img_width
        height_ratio = (letter[1] - 40) / img_height
        scale = min(width_ratio, height_ratio)

        new_width = img_width * scale
        new_height = img_height * scale

        # Center the image on the page
        x_centered = (letter[0] - new_width) / 2
        y_centered = (letter[1] - new_height) / 2

        # Convert PIL Image to ImageReader object
        img_reader = ImageReader(img)

        # Draw image using ImageReader
        c.drawImage(img_reader, x_centered, y_centered,
                    width=new_width, height=new_height)
        c.showPage()  # This ensures each image is on a new page

    c.save()
    pdf_buffer.seek(0)
    return pdf_buffer


def compress_pdf(pdf_buffer, target_size):
    reader = PdfReader(pdf_buffer)
    writer = PdfWriter()

    for page in reader.pages:
        writer.add_page(page)

    for i in range(100, 0, -10):  # Start from 100% quality, reduce by 10% each iteration
        output = io.BytesIO()
        writer.write(output)
        output.seek(0)
        if output.getbuffer().nbytes <= target_size:
            return output
        writer.remove_links()  # Remove hyperlinks to reduce size

    logger.warning("Unable to compress PDF to target size")
    return output  # Return the smallest achievable size


@app.route('/generate_pdf', methods=['POST'])
def generate_pdf():
    if 'files' not in request.files:
        return jsonify({"error": "No file part"}), 400

    files = request.files.getlist('files')
    if not files or files[0].filename == '':
        return jsonify({"error": "No selected files"}), 400

    images = []
    quality = 85  # Start with high quality

    while quality >= 30:  # Don't go below 30% quality
        images = []
        for file in files:
            if file and allowed_file(file.filename):
                try:
                    img = Image.open(file.stream)
                    processed_buffer = process_image(img, quality)
                    processed_img = Image.open(processed_buffer)
                    images.append(processed_img)
                except Exception as e:
                    logger.exception(
                        f"Error processing image: {file.filename}")
                    return jsonify({"error": str(e)}), 500
            else:
                return jsonify({"error": f"File type not allowed: {file.filename}"}), 400

        try:
            pdf_buffer = create_pdf(images)
            logger.debug(f"PDF created, size: {
                         pdf_buffer.getbuffer().nbytes} bytes")

            compressed_pdf = compress_pdf(pdf_buffer, MAX_FILE_SIZE)
            logger.debug(f"PDF compressed, size: {
                         compressed_pdf.getbuffer().nbytes} bytes")

            if compressed_pdf.getbuffer().nbytes <= MAX_FILE_SIZE:
                compressed_pdf.seek(0)
                return send_file(
                    compressed_pdf,
                    mimetype='application/pdf',
                    as_attachment=True,
                    download_name='processed_images.pdf'
                )
            else:
                quality -= 10  # Reduce quality and try again
                logger.debug(f"Reducing image quality to {quality}")
        except Exception as e:
            logger.exception("An error occurred during PDF generation")
            return jsonify({"error": str(e)}), 500

    return jsonify({"error": "Unable to compress PDF to under 4MB"}), 400


@app.route('/', methods=['GET', 'POST'])
def upload_file():
    if request.method == 'POST':
        if 'file' not in request.files:
            return jsonify({"error": "No file part"}), 400
        file = request.files['file']
        if file.filename == '':
            return jsonify({"error": "No selected file"}), 400
        if file and allowed_file(file.filename):
            try:
                image = Image.open(file.stream)
                processed_buffer = process_image(image)
                validation_results = validate_image(processed_buffer)

                if all(validation_results.values()):
                    processed_buffer.seek(0)
                    return Response(
                        processed_buffer,
                        mimetype='image/jpeg',
                        headers={
                            "Content-Disposition": "attachment; filename=processed_image.jpg"
                        }
                    )
                else:
                    return jsonify({
                        "message": "Image processed but doesn't meet all criteria",
                        "validation_results": validation_results
                    }), 200
            except Exception as e:
                return jsonify({"error": str(e)}), 500
        else:
            return jsonify({"error": "File type not allowed"}), 400

    return render_template('index.html')


def merge_pdfs(pdf_files):
    merger = PdfMerger()

    for pdf in pdf_files:
        merger.append(pdf)

    output = io.BytesIO()
    merger.write(output)
    merger.close()

    output.seek(0)
    return output


def compress_merged_pdf(pdf_buffer, target_size):
    reader = PdfReader(pdf_buffer)
    writer = PdfWriter()

    for page in reader.pages:
        writer.add_page(page)

    for i in range(100, 0, -10):  # Start from 100% quality, reduce by 10% each iteration
        output = io.BytesIO()
        writer.write(output)
        output.seek(0)
        if output.getbuffer().nbytes <= target_size:
            return output
        # Reduce quality of images in the PDF
        for page in writer.pages:
            for img in page.images:
                img.replace(img.image, quality=i)

    logger.warning("Unable to compress merged PDF to target size")
    return output  # Return the smallest achievable size


@app.route('/merge_pdfs', methods=['POST'])
def merge_pdf_files():
    if 'files' not in request.files:
        return jsonify({"error": "No file part"}), 400

    files = request.files.getlist('files')
    if not files or files[0].filename == '':
        return jsonify({"error": "No selected files"}), 400

    pdf_files = []
    for file in files:
        if file and file.filename.lower().endswith('.pdf'):
            pdf_files.append(file)
        else:
            return jsonify({"error": f"File type not allowed: {file.filename}. Only PDF files are accepted."}), 400

    try:
        merged_pdf = merge_pdfs(pdf_files)
        logger.debug(f"PDFs merged, size: {
                     merged_pdf.getbuffer().nbytes} bytes")

        if merged_pdf.getbuffer().nbytes > MAX_FILE_SIZE:
            compressed_pdf = compress_merged_pdf(merged_pdf, MAX_FILE_SIZE)
            logger.debug(f"Merged PDF compressed, size: {
                         compressed_pdf.getbuffer().nbytes} bytes")

            if compressed_pdf.getbuffer().nbytes > MAX_FILE_SIZE:
                return jsonify({"error": "Unable to compress merged PDF to under 4MB"}), 400

            merged_pdf = compressed_pdf

        merged_pdf.seek(0)
        return send_file(
            merged_pdf,
            mimetype='application/pdf',
            as_attachment=True,
            download_name='merged.pdf'
        )
    except Exception as e:
        logger.exception("An error occurred during PDF merging")
        return jsonify({"error": str(e)}), 500


if __name__ == '__main__':
    app.run(debug=True)
