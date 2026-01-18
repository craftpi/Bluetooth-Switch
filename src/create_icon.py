from PIL import Image, ImageDraw, ImageFont
import os
import sys

# Versuch, eine Standard-Schriftart zu finden
def get_font(size):
    font_names = ["arialbd.ttf", "arial.ttf", "calibrib.ttf", "calibri.ttf", "segoeuib.ttf", "segoeui.ttf", "DejaVuSans-Bold.ttf", "FreeSansBold.ttf"]
    for font_name in font_names:
        try:
            # Versuche, die Schriftart zu laden
            return ImageFont.truetype(font_name, size)
        except IOError:
            continue
    # Fallback, falls keine der Schriftarten gefunden wird
    print("Warnung: Keine passende TTF-Schriftart gefunden. Nutze Standard-Bitmap-Schrift.")
    return ImageFont.load_default()

def create_onenote_remote_icon():
    # Größe für das Icon (256x256 ist Standard für Windows Icons)
    size = (256, 256)
    # Transparenter Hintergrund
    image = Image.new('RGBA', size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)

    # Farben aus dem Bild
    dark_bg_color = (50, 30, 100) # Dunkles Blau/Lila
    white = (255, 255, 255)

    # 1. Hintergrund: Abgerundetes Rechteck (Squircle)
    margin = 10
    # Pillow's rounded_rectangle ist ab Version 8.2.0 verfügbar
    if hasattr(draw, 'rounded_rectangle'):
        draw.rounded_rectangle(
            [margin, margin, size[0]-margin, size[1]-margin],
            radius=50,
            fill=dark_bg_color
        )
    else:
        # Fallback für ältere Pillow Versionen: Normales Rechteck mit abgerundeten Ecken workaround
        draw.rectangle(
            [margin+20, margin, size[0]-margin-20, size[1]-margin], fill=dark_bg_color
        )
        draw.rectangle(
            [margin, margin+20, size[0]-margin, size[1]-margin-20], fill=dark_bg_color
        )
        draw.ellipse([margin, margin, margin+40, margin+40], fill=dark_bg_color)
        draw.ellipse([size[0]-margin-40, margin, size[0]-margin, margin+40], fill=dark_bg_color)
        draw.ellipse([margin, size[1]-margin-40, margin+40, size[1]-margin], fill=dark_bg_color)
        draw.ellipse([size[0]-margin-40, size[1]-margin-40, size[0]-margin, size[1]-margin], fill=dark_bg_color)


    # 2. Das "RS" Logo (statt N)
    logo_text = "RS"
    logo_font_size = 110 # Großes Logo
    logo_font = get_font(logo_font_size)

    # Textgröße berechnen, um ihn zu zentrieren (etwas höher als die Mitte)
    bbox = draw.textbbox((0, 0), logo_text, font=logo_font)
    text_width = bbox[2] - bbox[0]
    text_height = bbox[3] - bbox[1]
    
    # Positionierung: Horizontal zentriert, Vertikal etwas nach oben verschoben
    text_x = (size[0] - text_width) // 2
    text_y = (size[1] - text_height) // 2 - 30 

    draw.text((text_x, text_y), logo_text, font=logo_font, fill=white)


    # 3. Weißes Bluetooth Symbol (mittig unter dem RS)
    bt_center_x = size[0] // 2
    bt_top_y = 160
    bt_bottom_y = 190
    bt_width = 10
    line_width = 4

    # Vertikale Linie
    draw.line((bt_center_x, bt_top_y, bt_center_x, bt_bottom_y), fill=white, width=line_width)
    # Obere Haken
    draw.line((bt_center_x, bt_top_y, bt_center_x + bt_width, bt_top_y + 10), fill=white, width=line_width)
    draw.line((bt_center_x + bt_width, bt_top_y + 10, bt_center_x - bt_width, bt_top_y + 20), fill=white, width=line_width)
    # Untere Haken
    draw.line((bt_center_x - bt_width, bt_top_y + 10, bt_center_x + bt_width, bt_top_y + 20), fill=white, width=line_width)
    draw.line((bt_center_x + bt_width, bt_top_y + 20, bt_center_x, bt_bottom_y), fill=white, width=line_width)


    # 4. Text "OneNote Remote" (weiß, unten mittig)
    app_text = "Remote-Switch"
    app_font_size = 24
    app_font = get_font(app_font_size)

    # Textgröße berechnen
    bbox_app = draw.textbbox((0, 0), app_text, font=app_font)
    app_text_width = bbox_app[2] - bbox_app[0]
    app_text_x = (size[0] - app_text_width) // 2
    app_text_y = 205

    draw.text((app_text_x, app_text_y), app_text, font=app_font, fill=white)

    # Speichern als ICO
    icon_path = "icon.ico"
    image.save(icon_path, format='ICO', sizes=[(256, 256), (128, 128), (64, 64), (48, 48), (32, 32), (16, 16)])
    print(f"Icon wurde erfolgreich erstellt: {icon_path}")

if __name__ == "__main__":
    create_onenote_remote_icon()