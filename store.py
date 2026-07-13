import streamlit as st
import pandas as pd
import os
import tempfile
from reportlab.lib import colors
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Spacer, Paragraph, PageBreak, Image, KeepTogether
from reportlab.lib.units import cm, mm
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.enums import TA_LEFT, TA_CENTER
from io import BytesIO
from PIL import Image as PILImage  # noqa: F401  (kept for compatibility; PIL used internally by qrcode/reportlab)
import qrcode

# --- PRINT-SAFE MARGIN ---
# Dedicated label/sticker printers (Zebra, TSC, Brother QL, etc.) usually have a
# small physical zone near the edges (feed rollers / cutter / gap sensor) that
# cannot be printed cleanly. Placing content exactly at 0mm often gets
# compressed or nudged by the printer firmware, which shows up as "overlapping"
# text/borders on the physical label even though the PDF itself looks fine.
# This margin keeps everything a safe distance from the edge.
PRINT_MARGIN = 1.5 * mm

# --- CELL PADDING ---
# ReportLab's default Table cell padding is 6pt left/right and 3pt top/bottom
# unless overridden. Defined explicitly so every width/height fit calculation
# below matches exactly what will actually be drawn - this is what guarantees
# text can never physically cross outside a cell/box.
CELL_LEFT_PAD = 6
CELL_RIGHT_PAD = 6
CELL_TOP_PAD = 3
CELL_BOTTOM_PAD = 3

# --- DEFAULT / REFERENCE LABEL SIZE ---
# This is the ORIGINAL design the layout was built around. Row heights below
# are stored as ratios of this reference content-box height, so that whatever
# content-box size the client picks for a given job, every row scales up or
# down together and the label keeps the exact same look - just bigger/smaller.
DEFAULT_STICKER_WIDTH_CM = 10.0
DEFAULT_STICKER_HEIGHT_CM = 15.0
DEFAULT_CONTENT_BOX_WIDTH_CM = 9.7   # = sticker width - 2*print margin
DEFAULT_CONTENT_BOX_HEIGHT_CM = 7.2

# Row-height ratios captured from the original fixed design (each row's share
# of the 7.2cm reference content-box height). These stay constant; only the
# content-box height changes, so every row grows/shrinks in the same proportion.
ROW_RATIOS = {
    'header':    1.2 / DEFAULT_CONTENT_BOX_HEIGHT_CM,   # Part No row
    'desc':      1.4 / DEFAULT_CONTENT_BOX_HEIGHT_CM,   # Description row
    'max_cap':   1.2 / DEFAULT_CONTENT_BOX_HEIGHT_CM,   # Max Capacity row
    'store_loc': 1.2 / DEFAULT_CONTENT_BOX_HEIGHT_CM,   # Store Location row
    'spacer':    0.3 / DEFAULT_CONTENT_BOX_HEIGHT_CM,   # gap before bottom row
    'mtm':       1.5 / DEFAULT_CONTENT_BOX_HEIGHT_CM,   # MTM + QR row
}

# --- START: LABEL STYLE FONT CAPS (used as the "max_font" ceiling for autofit) ---
LABEL_FONT_CAPS = {
    'part_no_label': 18,
    'desc_label': 12,
    'max_cap_label': 12,
    'store_loc_label': 12,
    'max_cap_value': 18,
}
# --- END ---


def get_dimensions(sticker_width_cm, sticker_height_cm, content_box_width_cm, content_box_height_cm):
    """
    Build a single dims object (all values in ReportLab points) from the
    user-chosen sizes. Everything downstream (row heights, column widths,
    font autofit, QR size) is derived from this - change the numbers here
    and the whole label re-scales while keeping the same format.
    """
    sticker_width = sticker_width_cm * cm
    sticker_height = sticker_height_cm * cm
    content_box_width = content_box_width_cm * cm
    content_box_height = content_box_height_cm * cm

    return {
        'sticker_width': sticker_width,
        'sticker_height': sticker_height,
        'sticker_pagesize': (sticker_width, sticker_height),
        'content_box_width': content_box_width,
        'content_box_height': content_box_height,
        'padded_content_width': content_box_width - (0.2 * cm),
        'row_heights': {k: v * content_box_height for k, v in ROW_RATIOS.items()},
    }


# --- START: INDIVIDUAL STYLE DEFINITIONS (labels use fixed small elements; big/variable text goes through fit_paragraph) ---

max_capacity_value_style = ParagraphStyle(
    name='MaxCapValue', fontName='Helvetica', fontSize=18,
    alignment=TA_CENTER, leading=20
)
# --- END: INDIVIDUAL STYLE DEFINITIONS ---


def safe_padding(dim, base_pad, min_avail=2):
    """
    Returns a padding value (<= base_pad) that always leaves at least
    `min_avail` points of usable space inside `dim`. This is what stops
    ReportLab from crashing with a "negative available width" error when a
    client picks a very small content box combined with many columns
    (e.g. 12 store-location cells inside a narrow box) - padding shrinks
    instead of the layout breaking.
    """
    max_pad = max(0, (dim - min_avail) / 2)
    return min(base_pad, max_pad)


def fit_paragraph(text, max_width, max_height, font_name='Helvetica-Bold',
                   max_font=24, min_font=6, leading_ratio=1.18, align=TA_CENTER,
                   left_pad=CELL_LEFT_PAD, right_pad=CELL_RIGHT_PAD,
                   top_pad=CELL_TOP_PAD, bottom_pad=CELL_BOTTOM_PAD):
    """
    Build a Paragraph that is GUARANTEED to fit inside (max_width x max_height),
    where max_width/max_height are the OUTER cell/box dimensions (padding is
    subtracted internally to match what ReportLab will really draw with).

    Instead of guessing a font size from character count, this actually
    measures the wrapped text with ReportLab's own Paragraph.wrap() and
    shrinks the font until it truly fits both dimensions. Because max_width/
    max_height are passed in fresh for every sticker (derived from whatever
    content-box size the client picked), this is also what makes the whole
    layout automatically re-scale to a new box size.
    """
    text = "" if text is None else str(text)
    avail_w = max(max_width - left_pad - right_pad, 1)
    avail_h = max(max_height - top_pad - bottom_pad, 1)

    # Font ceiling also scales down for very small boxes so labels don't
    # start bigger than the box itself when a client picks a tiny content box.
    effective_max_font = max(min(max_font, int(avail_h)), min_font)

    chosen_style = None
    for font_size in range(effective_max_font, min_font - 1, -1):
        style = ParagraphStyle(
            name=f'fit_{font_name}_{font_size}_{abs(hash(text)) % 100000}',
            fontName=font_name, fontSize=font_size,
            leading=font_size * leading_ratio, alignment=align,
            wordWrap='CJK', splitLongWords=1,
        )
        p = Paragraph(text.replace('\n', '<br/>'), style)
        w, h = p.wrap(avail_w, 100000)
        if h <= avail_h:
            chosen_style = style
            break

    if chosen_style is None:
        # Even the smallest font doesn't fit vertically - use it anyway
        # (best effort) rather than letting an earlier, larger size overflow.
        chosen_style = ParagraphStyle(
            name=f'fit_{font_name}_min_{abs(hash(text)) % 100000}',
            fontName=font_name, fontSize=min_font,
            leading=min_font * leading_ratio, alignment=align,
            wordWrap='CJK', splitLongWords=1,
        )

    return Paragraph(text.replace('\n', '<br/>'), chosen_style)


def clean_number_format(value):
    """Clean number formatting to preserve integers and handle decimals properly."""
    if pd.isna(value) or value == '': return ''
    if isinstance(value, str):
        value = value.strip()
        if value == '': return ''
        try:
            num_value = float(value)
            return str(int(num_value)) if num_value.is_integer() else str(num_value)
        except ValueError:
            return value
    if isinstance(value, (int, float)):
        return str(int(value)) if float(value).is_integer() else str(value)
    return str(value)

def generate_qr_code(data_string, target_size):
    """Generate a QR code from the given data string, sized to fit target_size (points)."""
    try:
        qr = qrcode.QRCode(version=1, error_correction=qrcode.constants.ERROR_CORRECT_M, box_size=10, border=4)
        qr.add_data(data_string)
        qr.make(fit=True)
        qr_img = qr.make_image(fill_color="black", back_color="white")
        img_buffer = BytesIO()
        qr_img.save(img_buffer, format='PNG')
        img_buffer.seek(0)
        return Image(img_buffer, width=target_size, height=target_size)
    except Exception as e:
        st.error(f"Error generating QR code: {e}")
        return None

def extract_store_location_data_from_excel(row_data, max_cells=12):
    """Extract up to 12 store location values dynamically."""
    values = []
    def get_clean_value(possible_names):
        upper_possible = [n.upper() for n in possible_names]
        col_map = {str(k).upper(): k for k in row_data.keys()}
        for name in upper_possible:
            if name in col_map:
                original_col = col_map[name]
                val = row_data[original_col]
                if pd.notna(val) and str(val).strip().lower() not in ['nan', 'none', 'null', '']:
                    return clean_number_format(val)
        return None
    for i in range(1, max_cells + 1):
        val = get_clean_value([f'Store Loc {i}', f'STORE_LOC_{i}', f'STORE LOC {i}'])
        if val:
            values.append(val)
    return values

def extract_line_location(row_data):
    """Extract the 'Line Location' value (a single free-text column, e.g.
    '12 M LE - ST-10 to 50 -KIT-TR-LH-01-A1'). This is used only inside the
    QR code payload — it is NOT printed on the visible sticker layout."""
    col_map = {str(k).upper(): k for k in row_data.keys()}
    for name in ['LINE LOCATION', 'LINE_LOCATION', 'LINELOCATION']:
        if name in col_map:
            val = row_data[col_map[name]]
            if pd.notna(val) and str(val).strip().lower() not in ['nan', 'none', 'null', '']:
                return str(val).strip()
    return ""

def create_single_sticker(row, part_no_col, desc_col, max_capacity_col, all_models, dims):
    """Create a single sticker layout with all its components, sized to `dims`."""
    part_no = clean_number_format(row.get(part_no_col, ""))
    desc = str(row.get(desc_col, "")).strip()
    max_capacity = clean_number_format(row.get(max_capacity_col, "")) if max_capacity_col else ""

    store_loc_values_raw = extract_store_location_data_from_excel(row)
    full_store_location = " ".join([str(v) for v in store_loc_values_raw if v])

    line_location = extract_line_location(row)

    mtm_quantities = row.get('aggregated_models', {})

    # QR code carries ONLY Store Location + Line Location.
    qr_data = f"Store Location: {full_store_location}\nLine Location: {line_location}"

    PADDED_CONTENT_WIDTH = dims['padded_content_width']
    CONTENT_BOX_WIDTH = dims['content_box_width']
    CONTENT_BOX_HEIGHT = dims['content_box_height']
    rh = dims['row_heights']

    sticker_content = []

    # Row heights scale with whatever content-box height was chosen for this
    # job (same ratios as the original fixed design - see ROW_RATIOS above).
    header_row_height = rh['header']
    desc_row_height = rh['desc']
    max_cap_row_height = rh['max_cap']
    store_loc_row_height = rh['store_loc']
    spacer_height = rh['spacer']
    mtm_row_height = rh['mtm']

    label_col_width = PADDED_CONTENT_WIDTH * 0.3
    value_col_width = PADDED_CONTENT_WIDTH * 0.7

    # Padding is computed per column/row so it can never exceed the space
    # available - this is what lets the box shrink to a small custom size
    # without ReportLab throwing a "negative available width" error. The
    # SAME pad values are then applied to the Table's own style below, so
    # the fit_paragraph sizing and the actual drawn layout always agree.
    label_h_pad = safe_padding(label_col_width, CELL_LEFT_PAD)
    value_h_pad = safe_padding(value_col_width, CELL_LEFT_PAD)
    min_main_row = min(header_row_height, desc_row_height, max_cap_row_height)
    main_v_pad = safe_padding(min_main_row, CELL_TOP_PAD)
    desc_extra_indent = max(0, min(10, value_col_width - 2 * value_h_pad - 4))

    part_no_label_p = fit_paragraph("Part No", label_col_width, header_row_height,
                                     font_name='Helvetica-Bold', max_font=LABEL_FONT_CAPS['part_no_label'], min_font=6,
                                     left_pad=label_h_pad, right_pad=label_h_pad, top_pad=main_v_pad, bottom_pad=main_v_pad)
    part_no_value_p = fit_paragraph(part_no, value_col_width, header_row_height,
                                     font_name='Helvetica-Bold', max_font=24, min_font=8,
                                     left_pad=value_h_pad, right_pad=value_h_pad, top_pad=main_v_pad, bottom_pad=main_v_pad)

    desc_label_p = fit_paragraph("Description", label_col_width, desc_row_height,
                                  font_name='Helvetica-Bold', max_font=LABEL_FONT_CAPS['desc_label'], min_font=6,
                                  left_pad=label_h_pad, right_pad=label_h_pad, top_pad=main_v_pad, bottom_pad=main_v_pad)
    desc_value_p = fit_paragraph(desc, value_col_width, desc_row_height,
                                  font_name='Helvetica', max_font=12, min_font=6, align=TA_LEFT,
                                  left_pad=value_h_pad + desc_extra_indent, right_pad=value_h_pad,
                                  top_pad=main_v_pad, bottom_pad=main_v_pad)

    max_cap_label_p = fit_paragraph("Max capacity", label_col_width, max_cap_row_height,
                                     font_name='Helvetica-Bold', max_font=LABEL_FONT_CAPS['max_cap_label'], min_font=6,
                                     left_pad=label_h_pad, right_pad=label_h_pad, top_pad=main_v_pad, bottom_pad=main_v_pad)
    max_cap_value_p = fit_paragraph(str(max_capacity), value_col_width, max_cap_row_height,
                                     font_name='Helvetica', max_font=LABEL_FONT_CAPS['max_cap_value'], min_font=8,
                                     left_pad=value_h_pad, right_pad=value_h_pad, top_pad=main_v_pad, bottom_pad=main_v_pad)

    main_table = Table([
        [part_no_label_p, part_no_value_p],
        [desc_label_p, desc_value_p],
        [max_cap_label_p, max_cap_value_p]
    ], colWidths=[label_col_width, value_col_width], rowHeights=[header_row_height, desc_row_height, max_cap_row_height])

    main_table.setStyle(TableStyle([
        ('GRID', (0, 0), (-1, -1), 1, colors.black),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('LEFTPADDING', (0, 0), (0, -1), label_h_pad), ('RIGHTPADDING', (0, 0), (0, -1), label_h_pad),
        ('LEFTPADDING', (1, 0), (1, -1), value_h_pad), ('RIGHTPADDING', (1, 0), (1, -1), value_h_pad),
        ('LEFTPADDING', (1, 1), (1, 1), value_h_pad + desc_extra_indent),
        ('TOPPADDING', (0, 0), (-1, -1), main_v_pad), ('BOTTOMPADDING', (0, 0), (-1, -1), main_v_pad),
    ]))
    sticker_content.append(main_table)

    store_loc_v_pad = safe_padding(store_loc_row_height, CELL_TOP_PAD)
    store_loc_label = fit_paragraph("Store Location", label_col_width, store_loc_row_height,
                                     font_name='Helvetica-Bold', max_font=LABEL_FONT_CAPS['store_loc_label'], min_font=6,
                                     left_pad=label_h_pad, right_pad=label_h_pad, top_pad=store_loc_v_pad, bottom_pad=store_loc_v_pad)
    store_loc_values = [v for v in store_loc_values_raw if v] or [""]
    inner_table_width = PADDED_CONTENT_WIDTH * 0.7
    num_cols = len(store_loc_values)
    inner_col_widths = [inner_table_width / num_cols] * num_cols if num_cols > 0 else [inner_table_width]
    single_loc_col_width = inner_table_width / max(num_cols, 1)
    # Padding shrinks automatically as more store-location values are packed
    # into the row (e.g. 12 codes in a narrow box) - this is what prevents
    # the "negative available width" crash while still guaranteeing every
    # value is measured and drawn with the exact same padding.
    loc_h_pad = safe_padding(single_loc_col_width, CELL_LEFT_PAD, min_avail=1)

    # Each store-location cell is a force-fit Paragraph (not a raw string).
    # Raw strings placed directly in a ReportLab Table draw as a single
    # unwrapped line with NO width constraint - that is what let long
    # location codes visually cross outside the sticker. fit_paragraph
    # guarantees every value wraps/shrinks to its own column and row.
    store_loc_cells = []
    for val in store_loc_values:
        cell_p = fit_paragraph(val, single_loc_col_width, store_loc_row_height,
                                font_name='Helvetica-Bold', max_font=14, min_font=5,
                                left_pad=loc_h_pad, right_pad=loc_h_pad,
                                top_pad=store_loc_v_pad, bottom_pad=store_loc_v_pad)
        store_loc_cells.append(cell_p)

    store_loc_inner_table = Table([store_loc_cells], colWidths=inner_col_widths, rowHeights=[store_loc_row_height])
    store_loc_inner_table.setStyle(TableStyle([('GRID', (0, 0), (-1, -1), 1, colors.black), ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
                                               ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
                                               ('LEFTPADDING', (0, 0), (-1, -1), loc_h_pad), ('RIGHTPADDING', (0, 0), (-1, -1), loc_h_pad),
                                               ('TOPPADDING', (0, 0), (-1, -1), store_loc_v_pad), ('BOTTOMPADDING', (0, 0), (-1, -1), store_loc_v_pad)]))

    store_loc_table = Table([[store_loc_label, store_loc_inner_table]], colWidths=[label_col_width, inner_table_width], rowHeights=[store_loc_row_height])
    store_loc_table.setStyle(TableStyle([('GRID', (0, 0), (-1, -1), 1, colors.black), ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
                                          ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
                                          ('LEFTPADDING', (0, 0), (0, -1), label_h_pad), ('RIGHTPADDING', (0, 0), (0, -1), label_h_pad),
                                          ('LEFTPADDING', (1, 0), (1, -1), 0), ('RIGHTPADDING', (1, 0), (1, -1), 0),
                                          ('TOPPADDING', (0, 0), (-1, -1), store_loc_v_pad), ('BOTTOMPADDING', (0, 0), (-1, -1), store_loc_v_pad)]))
    sticker_content.append(store_loc_table)
    sticker_content.append(Spacer(1, spacer_height))

    bottom_row_width = PADDED_CONTENT_WIDTH
    mtm_section_width = bottom_row_width * 0.7
    qr_section_width = bottom_row_width * 0.3

    max_models = 5
    mtm_box_width = mtm_section_width / max_models
    mtm_header_row_h = mtm_row_height / 2
    mtm_value_row_h = mtm_row_height / 2

    mtm_h_pad = safe_padding(mtm_box_width, CELL_LEFT_PAD, min_avail=1)
    mtm_v_pad = safe_padding(min(mtm_header_row_h, mtm_value_row_h), CELL_TOP_PAD)

    headers, values = [], []
    for model_name in all_models:
        header_p = fit_paragraph(model_name, mtm_box_width, mtm_header_row_h,
                                  font_name='Helvetica-Bold', max_font=14, min_font=5,
                                  left_pad=mtm_h_pad, right_pad=mtm_h_pad, top_pad=mtm_v_pad, bottom_pad=mtm_v_pad)
        headers.append(header_p)

        qty_val = mtm_quantities.get(model_name, "") if model_name else ""
        qty_str = clean_number_format(qty_val) if qty_val else ""
        value_p = fit_paragraph(qty_str, mtm_box_width, mtm_value_row_h,
                                 font_name='Helvetica-Bold', max_font=14, min_font=5,
                                 left_pad=mtm_h_pad, right_pad=mtm_h_pad, top_pad=mtm_v_pad, bottom_pad=mtm_v_pad)
        values.append(value_p)

    mtm_table = Table([headers, values], colWidths=[mtm_box_width] * max_models, rowHeights=[mtm_header_row_h, mtm_value_row_h])
    mtm_table.setStyle(TableStyle([
        ('GRID', (0, 0), (-1, -1), 1, colors.black),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('LEFTPADDING', (0, 0), (-1, -1), mtm_h_pad), ('RIGHTPADDING', (0, 0), (-1, -1), mtm_h_pad),
        ('TOPPADDING', (0, 0), (-1, -1), mtm_v_pad), ('BOTTOMPADDING', (0, 0), (-1, -1), mtm_v_pad),
    ]))

    # QR is capped to whatever is smaller of its column width or the row
    # height (minus a little breathing room) so it always fits its cell,
    # no matter how small/large the chosen content box is.
    qr_target_size = max(min(qr_section_width - 6, mtm_row_height - 6), 8)
    qr_image = generate_qr_code(qr_data, qr_target_size)
    qr_element = qr_image if qr_image else Paragraph("QR", ParagraphStyle(name='qr-placeholder', alignment=TA_CENTER))

    bottom_row_table = Table(
        [[mtm_table, qr_element]],
        colWidths=[mtm_section_width, qr_section_width],
        rowHeights=[mtm_row_height]
    )
    bottom_row_table.setStyle(TableStyle([
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('ALIGN', (0, 0), (0, 0), 'LEFT'),
        ('ALIGN', (-1, -1), (-1, -1), 'CENTER'),
        ('LEFTPADDING', (0, 0), (-1, -1), 0),
        ('RIGHTPADDING', (0, 0), (-1, -1), 0),
    ]))

    sticker_content.append(bottom_row_table)

    sticker_table = Table([[sticker_content]], colWidths=[CONTENT_BOX_WIDTH], rowHeights=[CONTENT_BOX_HEIGHT])
    sticker_table.setStyle(TableStyle([
        ('BOX', (0, 0), (-1, -1), 2, colors.black),
        ('VALIGN', (0,0), (-1,-1), 'TOP'),
        ('ALIGN', (0,0), (-1,-1), 'CENTER'),
        ('LEFTPADDING', (0, 0), (-1, -1), 0), ('RIGHTPADDING', (0, 0), (-1, -1), 0),
        ('TOPPADDING', (0, 0), (-1, -1), 0), ('BOTTOMPADDING', (0, 0), (-1, -1), 0),
    ]))

    return KeepTogether([sticker_table])

def generate_sticker_labels(excel_file_path, output_pdf_path, dims, status_callback=None):
    if status_callback: status_callback(f"Reading file: {excel_file_path}")
    try:
        df = pd.read_csv(excel_file_path, keep_default_na=False) if excel_file_path.lower().endswith('.csv') else pd.read_excel(excel_file_path, keep_default_na=False, engine='openpyxl')
        if df.empty:
            if status_callback: status_callback("❌ Error: The uploaded file is empty.")
            return None
        if status_callback: status_callback(f"✅ Successfully read {len(df)} rows. Processing data...")
    except Exception as e:
        if status_callback: status_callback(f"❌ Error reading file: {e}. Please ensure it is a valid Excel or CSV file.")
        return None

    original_columns = df.columns.tolist()

    if len(original_columns) < 2:
        if status_callback: status_callback("❌ Error: File must have at least 2 columns (Part Number, Description).")
        return None

    part_no_col = next((c for c in original_columns if 'PART' in str(c).upper() and 'NO' in str(c).upper()), original_columns[0])
    desc_col = next((c for c in original_columns if 'DESC' in str(c).upper()), original_columns[1])
    max_capacity_col = next((c for c in original_columns if 'MAX' in str(c).upper() and 'CAPACITY' in str(c).upper()), None)

    model_cols_original = original_columns[2:7] if len(original_columns) >= 7 else original_columns[2:]

    all_models = []
    for col in model_cols_original:
        col_str = str(col).strip()
        if pd.isna(col) or col_str == '' or col_str.lower().startswith('unnamed:'):
            all_models.append('')
        else:
            all_models.append(col_str.upper())

    model_mapping = list(zip(model_cols_original, all_models))

    def get_model_quantities(row, mapping):
        model_quantities = {}
        for original_col, cleaned_model_name in mapping:
            if not cleaned_model_name: continue
            if original_col in row and pd.notna(row[original_col]) and row[original_col] != '':
                qty = clean_number_format(row[original_col])
                if qty and str(qty) != '0':
                    model_quantities[cleaned_model_name] = qty
        return model_quantities

    df['aggregated_models'] = df.apply(lambda row: get_model_quantities(row, model_mapping), axis=1)

    doc = SimpleDocTemplate(
        output_pdf_path, pagesize=dims['sticker_pagesize'],
        topMargin=PRINT_MARGIN, bottomMargin=PRINT_MARGIN,
        leftMargin=PRINT_MARGIN, rightMargin=PRINT_MARGIN
    )
    all_elements = []
    total_stickers = len(df)

    current_row_index = 0
    try:
        for i in range(total_stickers):
            current_row_index = i + 2
            if status_callback: status_callback(f"⚙️ Creating sticker for row {current_row_index}...")

            row_data = df.iloc[i].to_dict()
            sticker = create_single_sticker(row_data, part_no_col, desc_col, max_capacity_col, all_models, dims)
            all_elements.append(sticker)

            if i < total_stickers - 1:
                all_elements.append(PageBreak())

        if status_callback: status_callback("Building final PDF...")
        doc.build(all_elements)
        if status_callback: status_callback("✅ PDF generated successfully!")
        return output_pdf_path

    except Exception as e:
        error_message = f"""❌ Error building PDF. The process failed at row {current_row_index} in your file.
        Please check the data in that row for issues like:
        - Very long text without spaces.
        - Invalid characters or data formats.
        - Technical Error: {e}"""
        if status_callback: status_callback(error_message)
        return None

def main():
    """Main Streamlit application"""
    st.set_page_config(page_title="Mezzanine Label Generator", page_icon="🏷️", layout="wide")
    st.title("🏷️ Mezzanine Label Generator")
    st.markdown("<p style='font-size:18px; font-style:italic; margin-top:-10px; text-align:left;'>Designed and Developed by Agilomatrix</p>", unsafe_allow_html=True)
    st.markdown("---")

    # --- Configurable label / content-box size ---
    # The client can change these per job. Every row inside the box scales
    # proportionally to whatever height/width is chosen here, so the format
    # (Part No / Description / Max Capacity / Store Location / MTM+QR rows)
    # stays identical - just resized to fit.
    #
    # NOTE ON NAMING: internally these are still called "sticker" (page) and
    # "content box" (label), to avoid touching working logic. Only the
    # on-screen text shown to users has been renamed:
    #   Sticker Size      -> Page Size
    #   Content Box Size  -> Label Size
    st.header("📐 Page Size & Label Size")
    size_col1, size_col2, size_col3, size_col4 = st.columns(4)
    with size_col1:
        sticker_width_cm = st.number_input("Page Width (cm)", min_value=3.0, max_value=50.0,
                                            value=DEFAULT_STICKER_WIDTH_CM, step=0.1)
    with size_col2:
        sticker_height_cm = st.number_input("Page Height (cm)", min_value=3.0, max_value=50.0,
                                             value=DEFAULT_STICKER_HEIGHT_CM, step=0.1)
    with size_col3:
        content_box_width_cm = st.number_input("Label Width (cm)", min_value=2.0, max_value=50.0,
                                                 value=DEFAULT_CONTENT_BOX_WIDTH_CM, step=0.1)
    with size_col4:
        content_box_height_cm = st.number_input("Label Height (cm)", min_value=2.0, max_value=50.0,
                                                  value=DEFAULT_CONTENT_BOX_HEIGHT_CM, step=0.1)

    max_allowed_width_cm = sticker_width_cm - (2 * PRINT_MARGIN / cm)
    max_allowed_height_cm = sticker_height_cm - (2 * PRINT_MARGIN / cm)
    size_warning = None
    if content_box_width_cm > max_allowed_width_cm:
        size_warning = f"Label Width ({content_box_width_cm}cm) is wider than the page allows ({max_allowed_width_cm:.2f}cm). It will be clamped."
        content_box_width_cm = max_allowed_width_cm
    if content_box_height_cm > max_allowed_height_cm:
        msg = f"Label Height ({content_box_height_cm}cm) is taller than the page allows ({max_allowed_height_cm:.2f}cm). It will be clamped."
        size_warning = (size_warning + " " + msg) if size_warning else msg
        content_box_height_cm = max_allowed_height_cm
    if size_warning:
        st.warning(f"⚠️ {size_warning}")

    dims = get_dimensions(sticker_width_cm, sticker_height_cm, content_box_width_cm, content_box_height_cm)
    st.caption(f"Page size: {sticker_width_cm}cm × {sticker_height_cm}cm  |  Label size: {content_box_width_cm}cm × {content_box_height_cm}cm")
    st.markdown("---")

    st.header("📁 File Upload")
    uploaded_file = st.file_uploader("Choose an Excel or CSV file", type=['xlsx', 'xls', 'csv'], help="Upload your file with parts data")

    if uploaded_file:
        with tempfile.NamedTemporaryFile(delete=False, suffix=os.path.splitext(uploaded_file.name)[1]) as tmp_file:
            tmp_file.write(uploaded_file.getvalue())
            temp_input_path = tmp_file.name

        st.success(f"✅ File uploaded: {uploaded_file.name}")
        try:
            preview_df = pd.read_excel(temp_input_path, header=0, engine='openpyxl').head(5) if uploaded_file.name.lower().endswith(('xlsx', 'xls')) else pd.read_csv(temp_input_path, header=0).head(5)
            st.subheader("📊 Data Preview (First 5 rows)")
            st.dataframe(preview_df, use_container_width=True)
        except Exception as e:
            st.error(f"Error previewing file: {e}")
            return

        st.subheader("🚀 Generate Labels")
        col1, col2 = st.columns([1, 1])

        with col1:
            if st.button("🏷️ Generate PDF Labels", type="primary", use_container_width=True):
                status_box = st.empty()
                def update_status(message):
                    status_box.text_area("Status", message, height=150)

                result_path = None
                try:
                    update_status("Starting label generation...")
                    with tempfile.NamedTemporaryFile(delete=False, suffix='.pdf') as tmp_output:
                        result_path = generate_sticker_labels(temp_input_path, tmp_output.name, dims, status_callback=update_status)

                    if result_path:
                        with open(result_path, 'rb') as pdf_file:
                            pdf_data = pdf_file.read()

                        st.download_button(
                            label="📥 Download PDF Labels", data=pdf_data,
                            file_name=f"mezzanine_labels_{os.path.splitext(uploaded_file.name)[0]}.pdf",
                            mime="application/pdf", use_container_width=True)
                except Exception as e:
                    update_status(f"❌ An unexpected critical error occurred: {str(e)}")
                finally:
                    if os.path.exists(temp_input_path): os.unlink(temp_input_path)
                    if result_path and os.path.exists(result_path): os.unlink(result_path)

        with col2:
            st.info(
                "**📋 File Format Requirements:**\n"
                "- Column A: Part Number\n"
                "- Column B: Part Description\n"
                "- **Columns C to G**: Bus Models (e.g., 'M', 'S') in the header.\n"
                "- *Blank/empty headers in C-G are handled correctly.*\n"
                "- Cells under C-G must contain the quantity for that model.\n"
                "- Optional: `Max Capacity`, `Store Loc...` columns."
            )
    else:
        st.info("👆 Please upload an Excel or CSV file to get started")
        st.subheader("✨ Features")
        col1, col2, col3 = st.columns(3)
        with col1: st.markdown(" **🏷️ Professional Labels** \n - Clean, readable design\n - Optimized for printing\n - **1 label per page, size set above**")
        with col2: st.markdown(" **📱 QR Code Integration** \n - Automatic QR code generation\n - Contains all part information\n - Easy scanning and tracking")
        with col3: st.markdown(" **🔄 Smart Data Handling** \n - Reads models directly from columns C-G\n - Ignores empty/unnamed columns\n - Aggregates data onto one sticker")

    st.markdown("---")
    st.markdown("<p style='text-align: center; color: gray; font-size: 14px;'>© 2025 Agilomatrix - Mezzanine Label Generator v10.0</p>", unsafe_allow_html=True)

if __name__ == "__main__":
    main()
