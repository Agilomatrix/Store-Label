import streamlit as st
import pandas as pd
import os
import re
from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Spacer, Paragraph, PageBreak, Image, KeepTogether
from reportlab.lib.units import cm
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.enums import TA_LEFT, TA_CENTER, TA_RIGHT
from io import BytesIO
import subprocess
import sys
import tempfile

# Define sticker dimensions
STICKER_WIDTH = 10 * cm
STICKER_HEIGHT = 15 * cm
STICKER_PAGESIZE = (STICKER_WIDTH, STICKER_HEIGHT)

# Define content box dimensions
CONTENT_BOX_WIDTH = 10 * cm
CONTENT_BOX_HEIGHT = 7.2 * cm

# Check for PIL and install if needed
try:
    from PIL import Image as PILImage
except ImportError:
    st.write("PIL not available. Installing...")
    subprocess.check_call([sys.executable, '-m', 'pip', 'install', 'pillow'])
    from PIL import Image as PILImage

# Check for QR code library and install if needed
try:
    import qrcode
except ImportError:
    st.write("qrcode not available. Installing...")
    subprocess.check_call([sys.executable, '-m', 'pip', 'install', 'qrcode'])
    import qrcode

# --- START: INDIVIDUAL STYLE DEFINITIONS ---

# Style for the "Part No" label
part_no_label_style = ParagraphStyle(
    name='PartNoLabel', fontName='Helvetica-Bold', fontSize=18,
    alignment=TA_CENTER, leading=18
)

# Style for the "Description" label
desc_label_style = ParagraphStyle(
    name='DescLabel', fontName='Helvetica-Bold', fontSize=12,
    alignment=TA_CENTER, leading=18
)

# Style for the "Max capacity" label
max_cap_label_style = ParagraphStyle(
    name='MaxCapLabel', fontName='Helvetica-Bold', fontSize=12,
    alignment=TA_CENTER, leading=18
)

# Style for the "Store Location" label
store_loc_label_style = ParagraphStyle(
    name='StoreLocLabel', fontName='Helvetica-Bold', fontSize=12,
    alignment=TA_CENTER, leading=18
)

# A very large, bold style specifically for the Part Number value
part_no_value_style = ParagraphStyle(
    name='PartNoValue', fontName='Helvetica-Bold', fontSize=24,
    alignment=TA_CENTER, leading=32, wordWrap='CJK', splitLongWords=1
)

def get_dynamic_desc_style(text):
    """Dynamically adjust font size for the description value."""
    length = len(str(text))
    if length <= 20: font_size = 12
    elif length <= 30: font_size = 10
    elif length <= 40: font_size = 8
    else: font_size = 14
    return ParagraphStyle(
        name='DescriptionDynamic', fontName='Helvetica', fontSize=font_size,
        alignment=TA_LEFT, leading=font_size + 2, wordWrap='CJK', splitLongWords=1,
    )

# Style for the Max Capacity value
max_capacity_value_style = ParagraphStyle(
    name='MaxCapValue', fontName='Helvetica', fontSize=18,
    alignment=TA_CENTER, leading=20
)
# --- END: INDIVIDUAL STYLE DEFINITIONS ---


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

def generate_qr_code(data_string):
    """Generate a QR code from the given data string."""
    try:
        qr = qrcode.QRCode(version=1, error_correction=qrcode.constants.ERROR_CORRECT_M, box_size=10, border=4)
        qr.add_data(data_string)
        qr.make(fit=True)
        qr_img = qr.make_image(fill_color="black", back_color="white")
        img_buffer = BytesIO()
        qr_img.save(img_buffer, format='PNG')
        img_buffer.seek(0)
        return Image(img_buffer, width=2*cm, height=2*cm)
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

def create_single_sticker(row, part_no_col, desc_col, max_capacity_col, all_models):
    """Create a single sticker layout with all its components."""
    part_no = clean_number_format(row.get(part_no_col, ""))
    desc = str(row.get(desc_col, "")).strip()
    max_capacity = clean_number_format(row.get(max_capacity_col, "")) if max_capacity_col else ""
    
    store_loc_values = extract_store_location_data_from_excel(row)
    full_store_location = " ".join([str(v) for v in store_loc_values if v])
    
    mtm_quantities = row.get('aggregated_models', {})
    qty_veh_string = ", ".join([f"{model}:{qty}" for model, qty in sorted(mtm_quantities.items()) if model])

    qr_data = (f"Part No: {part_no}\nDescription: {desc}\nMax Capacity: {max_capacity}\n"
               f"Store Location: {full_store_location}\nQTY/VEH: {qty_veh_string}")
    qr_image = generate_qr_code(qr_data)
    
    PADDED_CONTENT_WIDTH = CONTENT_BOX_WIDTH - (0.2 * cm) 
    sticker_content = []
    
    header_row_height, desc_row_height, max_cap_row_height, store_loc_row_height = 1.2*cm, 1.4*cm, 1.2*cm, 1.2*cm

    # Create Paragraph objects using their unique, individual styles
    part_no_label_p = Paragraph("Part No", part_no_label_style)
    part_no_value_p = Paragraph(part_no, part_no_value_style)
    
    desc_label_p = Paragraph("Description", desc_label_style)
    desc_value_p = Paragraph(desc, get_dynamic_desc_style(desc))
    
    max_cap_label_p = Paragraph("Max capacity", max_cap_label_style)
    max_cap_value_p = Paragraph(str(max_capacity), max_capacity_value_style)

    main_table = Table([
        [part_no_label_p, part_no_value_p],
        [desc_label_p, desc_value_p],
        [max_cap_label_p, max_cap_value_p]
    ], colWidths=[PADDED_CONTENT_WIDTH*0.3, PADDED_CONTENT_WIDTH*0.7], rowHeights=[header_row_height, desc_row_height, max_cap_row_height])
    
    main_table.setStyle(TableStyle([
        ('GRID', (0, 0), (-1, -1), 1, colors.black),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('LEFTPADDING', (1, 1), (1, 1), 10),
    ]))
    sticker_content.append(main_table)

    store_loc_label = Paragraph("Store Location", store_loc_label_style)
    store_loc_values = [v for v in extract_store_location_data_from_excel(row) if v] or [""]
    inner_table_width = PADDED_CONTENT_WIDTH * 0.7 # Adjusted to the new 70% width
    num_cols = len(store_loc_values)
    inner_col_widths = [inner_table_width / num_cols] * num_cols if num_cols > 0 else [inner_table_width]
    
    store_loc_inner_table = Table([store_loc_values], colWidths=inner_col_widths, rowHeights=[store_loc_row_height])
    store_loc_inner_table.setStyle(TableStyle([('GRID', (0, 0), (-1, -1), 1, colors.black), ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
                                               ('ALIGN', (0, 0), (-1, -1), 'CENTER'), ('FONTSIZE', (0, 0), (-1, -1), 14),
                                               ('FONTNAME', (0, 0), (-1, -1), 'Helvetica-Bold')]))
    
    store_loc_table = Table([[store_loc_label, store_loc_inner_table]], colWidths=[PADDED_CONTENT_WIDTH*0.3, inner_table_width], rowHeights=[store_loc_row_height])
    store_loc_table.setStyle(TableStyle([('GRID', (0, 0), (-1, -1), 1, colors.black), ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
                                          ('ALIGN', (0, 0), (-1, -1), 'CENTER')]))
    sticker_content.append(store_loc_table)
    sticker_content.append(Spacer(1, 0.3*cm))
    
    bottom_row_width = PADDED_CONTENT_WIDTH
    mtm_section_width = bottom_row_width * 0.7
    qr_section_width = bottom_row_width * 0.3

    max_models = 5
    mtm_row_height = 1.5 * cm
    mtm_box_width = mtm_section_width / max_models

    headers, values = [], []
    for model_name in all_models:
        headers.append(Paragraph(f"<b>{model_name}</b>", ParagraphStyle(name='model_header', fontSize=14, alignment=TA_CENTER)))
        qty_val = mtm_quantities.get(model_name, "") if model_name else ""
        values.append(Paragraph(f"<b>{clean_number_format(qty_val)}</b>" if qty_val else "",
            ParagraphStyle(name=f"Qty_{model_name}", fontName='Helvetica-Bold', fontSize=14, alignment=TA_CENTER)))
    
    mtm_table = Table([headers, values], colWidths=[mtm_box_width] * max_models, rowHeights=[mtm_row_height/2, mtm_row_height/2])
    mtm_table.setStyle(TableStyle([
        ('GRID', (0, 0), (-1, -1), 1, colors.black),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
    ]))

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

def generate_sticker_labels(excel_file_path, output_pdf_path, status_callback=None):
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

    doc = SimpleDocTemplate(output_pdf_path, pagesize=STICKER_PAGESIZE, topMargin=0, bottomMargin=0, leftMargin=0, rightMargin=0)
    all_elements = []
    total_stickers = len(df)
    
    current_row_index = 0
    try:
        for i in range(total_stickers):
            current_row_index = i + 2
            if status_callback: status_callback(f"⚙️ Creating sticker for row {current_row_index}...")
            
            row_data = df.iloc[i].to_dict()
            sticker = create_single_sticker(row_data, part_no_col, desc_col, max_capacity_col, all_models)
            all_elements.append(sticker)
            
            if i < total_stickers - 1:
                all_elements.append(PageBreak())

        if status_callback: status_callback("Building final PDF...")
        doc.build(all_elements)
        if status_callback: status_callback(f"✅ PDF generated successfully!")
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
                        result_path = generate_sticker_labels(temp_input_path, tmp_output.name, status_callback=update_status)
                    
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
        with col1: st.markdown(" **🏷️ Professional Labels** \n - Clean, readable design\n - Optimized for printing\n - **1 label per page (10x15 cm)**")
        with col2: st.markdown(" **📱 QR Code Integration** \n - Automatic QR code generation\n - Contains all part information\n - Easy scanning and tracking")
        with col3: st.markdown(" **🔄 Smart Data Handling** \n - Reads models directly from columns C-G\n - Ignores empty/unnamed columns\n - Aggregates data onto one sticker")

    st.markdown("---")
    st.markdown("<p style='text-align: center; color: gray; font-size: 14px;'>© 2025 Agilomatrix - Mezzanine Label Generator v8.0 (Final)</p>", unsafe_allow_html=True)

if __name__ == "__main__":
    main()
