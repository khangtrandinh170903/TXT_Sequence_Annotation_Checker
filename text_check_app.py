import re
import io
import hashlib
import zipfile
import streamlit as st

from pathlib import Path
from difflib import SequenceMatcher


# ============================================================
# PAGE CONFIGURATION
# ============================================================

st.set_page_config(
    page_title="TXT Annotation Checker",
    page_icon="📝",
    layout="wide"
)


# ============================================================
# CONFIGURATION
# ============================================================

STOP_MARKER = "preannotation quality report"

LABEL_PREFIXES = [
    "shopping_basket_full",
    "shopping_basket_empty",
    "p_shopping_bag",
    "hand",
    "obj_c",
    "gun",
    "p_personal",
    "bad_track",
    "hidden_hand"
]

TRAILING_PUNCTUATION = ".,;:!?\"'`~)]}》」』】"

FIXED_LABELS = {"face"}
ACTIONS = {"remove", "maintain"}

FUZZY_THRESHOLD = 0.82
MIN_SCORE_DIFFERENCE = 0.06

INTEGER_PATTERN = re.compile(r"[0-9]+\Z")

EDGE_CHARACTERS = (
    " \t\v\f\r\n"
    "\u00a0\u200b\u200c\u200d\u2060\ufeff"
)


# ============================================================
# LABEL VALIDATION
# ============================================================

def is_valid_label(label):

    if label in FIXED_LABELS:
        return True

    match = re.fullmatch(r"(.+)_([0-9]+)", label)

    if match:
        return match.group(1) in LABEL_PREFIXES

    return False


def is_valid_second_field(value):

    return value in ACTIONS or is_valid_label(value)


# ============================================================
# FUZZY SPELLING CORRECTION
# ============================================================

def get_best_match(word, candidates):

    scores = []

    for candidate in candidates:

        score = SequenceMatcher(
            None, word, candidate
        ).ratio()

        scores.append((candidate, score))

    scores.sort(key=lambda x: x[1], reverse=True)

    if not scores:
        return None, 0

    best_match, best_score = scores[0]

    if best_score < FUZZY_THRESHOLD:
        return None, best_score

    if len(scores) > 1:

        second_best_score = scores[1][1]

        if best_score - second_best_score < MIN_SCORE_DIFFERENCE:
            return None, best_score

    return best_match, best_score


def auto_correct_label(value, allow_actions=False):

    if is_valid_label(value):
        return value, False

    if allow_actions and value in ACTIONS:
        return value, False

    # --------------------------------------------------------
    # SPECIAL CASE: obj_c_x common spelling mistakes
    # --------------------------------------------------------

    # Case 1: obj_x -> obj_c_x
    # Example: obj_25 -> obj_c_25

    match = re.fullmatch(r"obj_([0-9]+)", value)

    if match:
        number = match.group(1)
        return f"obj_c_{number}", True

    # Case 2: obj_cx -> obj_c_x
    # Example: obj_c25 -> obj_c_25

    match = re.fullmatch(r"obj_c([0-9]+)", value)

    if match:
        number = match.group(1)
        return f"obj_c_{number}", True

    # --------------------------------------------------------
    # REGULAR FUZZY SPELLING CORRECTION
    # --------------------------------------------------------

    # Labels with integer suffixes.
    match = re.fullmatch(r"(.+)_([0-9]+)", value)

    if match:

        prefix = match.group(1)
        number = match.group(2)

        best_match, score = get_best_match(
            prefix, LABEL_PREFIXES
        )

        if best_match is not None:
            return f"{best_match}_{number}", True

        return value, False

    # Fixed labels and special commands.
    candidates = list(FIXED_LABELS)

    if allow_actions:
        candidates.extend(ACTIONS)

    best_match, score = get_best_match(
        value, candidates
    )

    if best_match is not None:
        return best_match, True

    return value, False



# ============================================================
# LINE UTILITIES
# ============================================================

def separate_line_ending(line):

    if line.endswith("\r\n"):
        return line[:-2], "\r\n"

    if line.endswith("\n") or line.endswith("\r"):
        return line[:-1], line[-1]

    return line, ""


def count_newlines(text):

    return len(re.findall(r"\r\n|\r|\n", text))


# ============================================================
# PROCESS TXT CONTENT
# ============================================================

def process_text(original_content):

    lines = original_content.splitlines(keepends=True)

    # Find stopping marker.
    marker_index = None

    for index, line in enumerate(lines):

        line_content, _ = separate_line_ending(line)

        if line_content.strip(EDGE_CHARACTERS).casefold() == STOP_MARKER:
            marker_index = index
            break

    if marker_index is None:

        prefix = original_content
        remaining_content = ""
        marker_found = False

    else:

        prefix = "".join(lines[:marker_index])
        remaining_content = "".join(lines[marker_index:])
        marker_found = True

    # Remove whitespace at beginning of file.
    
    # --------------------------------------------------------
    # Remove blank lines and insufficient-data lines.
    # --------------------------------------------------------

    removed_blank_lines = 0
    removed_insufficient_lines = 0

    uppercase_corrections = 0
    formatting_corrections = 0
    punctuation_corrections = 0

    spelling_corrections = []
    errors = []

    normalized_lines = []
    normalized_line_numbers = []

    for index, line in enumerate(
        prefix.splitlines(keepends=True)
    ):

        # Preserve original line numbers.
        line_number = index + 1

        body, line_ending = separate_line_ending(line)

        cleaned_body = body.rstrip(EDGE_CHARACTERS)

        cleaned_body = cleaned_body.rstrip(TRAILING_PUNCTUATION)

        # Count punctuation corrections.
        if cleaned_body != body.rstrip(EDGE_CHARACTERS):
            punctuation_corrections += 1

        body = cleaned_body

        # ----------------------------------------------------
        # Case 1: Remove blank lines.
        # ----------------------------------------------------

        if body.strip(EDGE_CHARACTERS) == "":

            removed_blank_lines += 1
            continue

        # ----------------------------------------------------
        # Split and normalize fields.
        # ----------------------------------------------------

        original_fields = body.split(",")

        fields = [
            field.strip(EDGE_CHARACTERS)
            for field in original_fields
        ]

        if fields != original_fields:
            formatting_corrections += 1

        # ----------------------------------------------------
        # Case 2: Remove lines with insufficient data.
        # ----------------------------------------------------

        # Fewer than 4 fields.
        if len(fields) < 4:

            removed_insufficient_lines += 1
            continue

        # Exactly 4 fields, but one or more are empty.
        if len(fields) == 4 and any(
            field == "" for field in fields
        ):

            removed_insufficient_lines += 1
            continue

        # ----------------------------------------------------
        # Convert the first two fields to lowercase.
        # ----------------------------------------------------

        for i in range(min(2, len(fields))):

            if fields[i] != fields[i].lower():

                uppercase_corrections += 1
                fields[i] = fields[i].lower()

        # ----------------------------------------------------
        # Correct first label.
        # ----------------------------------------------------

        if len(fields) >= 1:

            old_label = fields[0]

            corrected, was_corrected = auto_correct_label(
                old_label,
                allow_actions=False
            )

            if was_corrected:

                fields[0] = corrected

                spelling_corrections.append({
                    "line": line_number,
                    "column": 1,
                    "old": old_label,
                    "new": corrected
                })

        # ----------------------------------------------------
        # Correct second label or command.
        # ----------------------------------------------------

        if len(fields) >= 2:

            old_label = fields[1]

            corrected, was_corrected = auto_correct_label(
                old_label,
                allow_actions=True
            )

            if was_corrected:

                fields[1] = corrected

                spelling_corrections.append({
                    "line": line_number,
                    "column": 2,
                    "old": old_label,
                    "new": corrected
                })

        # Save corrected line.
        normalized_lines.append(
            ",".join(fields) + line_ending
        )

        normalized_line_numbers.append(line_number)

    cleaned_prefix = "".join(normalized_lines)

    # Everything from the stopping marker onwards stays unchanged.
    cleaned_content = cleaned_prefix + remaining_content

    # --------------------------------------------------------
    # Validate syntax.
    # --------------------------------------------------------

    checked_lines = 0

    for index, line in enumerate(normalized_lines):

        line_number = normalized_line_numbers[index]

        body, _ = separate_line_ending(line)

        checked_lines += 1

        fields = body.split(",")

        if len(fields) != 4:

            errors.append({
                "line": line_number,
                "message": (
                    f"Expected 4 fields, found {len(fields)}."
                )
            })

            continue

        label_a, label_b, number_a, number_b = fields

        if not is_valid_label(label_a):

            errors.append({
                "line": line_number,
                "message": f"Invalid first label: '{label_a}'."
            })

        if not is_valid_second_field(label_b):

            errors.append({
                "line": line_number,
                "message": (
                    f"Invalid second label or command: '{label_b}'."
                )
            })

        if not INTEGER_PATTERN.fullmatch(number_a):

            errors.append({
                "line": line_number,
                "message": (
                    f"Invalid integer in column 3: '{number_a}'."
                )
            })

        if not INTEGER_PATTERN.fullmatch(number_b):

            errors.append({
                "line": line_number,
                "message": (
                    f"Invalid integer in column 4: '{number_b}'."
                )
            })


    # Return processing results.
    stats = {
        "checked_lines": checked_lines,
        "uppercase_corrections": uppercase_corrections,
        "formatting_corrections": formatting_corrections,
        "punctuation_corrections": punctuation_corrections,
        "spelling_corrections": len(spelling_corrections),
        "removed_blank_lines": removed_blank_lines,
        "removed_insufficient_lines": removed_insufficient_lines,
        "error_count": len(errors),
        "marker_found": marker_found
    }

    return (
        cleaned_content,
        stats,
        spelling_corrections,
        errors
    )


# ============================================================
# GENERATE PROCESSING REPORT
# ============================================================

def generate_report(results):

    report = []

    report.append("TXT ANNOTATION CHECKER - PROCESSING REPORT")
    report.append("=" * 60)

    for result in results:

        report.append("")
        report.append(f"FILE: {result['original_name']}")
        report.append("-" * 60)

        stats = result["stats"]

        report.append(
            f"Checked lines: {stats['checked_lines']}"
        )

        report.append(
            f"Spelling corrections: {stats['spelling_corrections']}"
        )

        report.append(
            f"Formatting corrections: {stats['formatting_corrections']}"
        )

        report.append(
            f"Uppercase corrections: {stats['uppercase_corrections']}"
        )

        report.append(
            f"Blank lines removed: {stats['removed_blank_lines']}"
        )

        report.append(
            f"Insufficient-data lines removed: "
            f"{stats['removed_insufficient_lines']}"
        )

        report.append(
            f"Punctuation corrections: "
            f"{stats['punctuation_corrections']}"
        )

        report.append(
            f"Remaining errors: {stats['error_count']}"
        )

        report.append(
            f"Stop marker found: {stats['marker_found']}"
        )

        if result["corrections"]:

            report.append("\nSPELLING CORRECTIONS:")

            for correction in result["corrections"]:

                report.append(
                    f"Line {correction['line']}, "
                    f"Column {correction['column']}: "
                    f"{correction['old']} -> {correction['new']}"
                )

        if result["errors"]:

            report.append("\nREMAINING ERRORS:")

            for error in result["errors"]:

                report.append(
                    f"Line {error['line']}: {error['message']}"
                )

        else:

            report.append("\nNo validation errors found.")

    return "\n".join(report)


# ============================================================
# CREATE DOWNLOAD FILES
# ============================================================

def create_download(results):

    # A single file is downloaded directly.
    if len(results) == 1:

        result = results[0]

        return (
            result["content"].encode("utf-8"),
            result["download_name"],
            "text/plain"
        )

    # Multiple files are packaged into a ZIP.
    zip_buffer = io.BytesIO()

    with zipfile.ZipFile(
        zip_buffer,
        "w",
        compression=zipfile.ZIP_DEFLATED
    ) as zip_file:

        for result in results:

            zip_file.writestr(
                result["download_name"],
                result["content"].encode("utf-8")
            )

        zip_file.writestr(
            "processing_report.txt",
            generate_report(results)
        )

    return (
        zip_buffer.getvalue(),
        "corrected_txt_files.zip",
        "application/zip"
    )


# ============================================================
# STREAMLIT USER INTERFACE
# ============================================================

st.title("📝 TXT Annotation Checker")

st.markdown(
    """
    Upload your annotation TXT files to automatically check and correct:

    - Spelling mistakes in labels.
    - Uppercase letters in labels.
    - Spaces around commas.
    - Leading and trailing whitespace.
    - Invalid line syntax.
    - Incorrect integer fields.

    Processing stops at `preannotation quality report`.
    """
)

st.divider()

# File uploader.
uploaded_files = st.file_uploader(
    "Upload TXT files",
    type=["txt"],
    accept_multiple_files=True,
    help="You can upload one TXT file or multiple TXT files."
)

# Invalidate old results if the uploaded files change.
if "results" not in st.session_state:
    st.session_state.results = None

current_signature = tuple(
    (
        file.name,
        hashlib.sha256(file.getvalue()).hexdigest()
    )
    for file in uploaded_files
) if uploaded_files else ()

if st.session_state.get("upload_signature") != current_signature:

    st.session_state.upload_signature = current_signature
    st.session_state.results = None


# Process button.
if st.button(
    "🔍 Check and Correct Files",
    type="primary",
    use_container_width=True
):

    if not uploaded_files:

        st.warning("Please upload at least one TXT file.")

    else:

        processed_results = []

        used_names = set()

        progress_bar = st.progress(0)

        status_text = st.empty()

        total_files = len(uploaded_files)

        for index, uploaded_file in enumerate(uploaded_files):

            status_text.write(
                f"Processing {uploaded_file.name} "
                f"({index + 1}/{total_files})..."
            )

            try:

                original_content = uploaded_file.getvalue().decode(
                    "utf-8"
                )

            except UnicodeDecodeError:

                st.error(
                    f"Could not read {uploaded_file.name}: "
                    "The file is not valid UTF-8 text."
                )

                progress_bar.progress(
                    (index + 1) / total_files
                )

                continue

            (
                corrected_content,
                stats,
                corrections,
                errors
            ) = process_text(original_content)

            # Generate a unique output filename.
            original_stem = Path(
                uploaded_file.name
            ).stem

            base_name = f"{original_stem}_corrected"

            download_name = f"{base_name}.txt"

            counter = 1

            while download_name.lower() in used_names:

                download_name = (
                    f"{base_name}_{counter}.txt"
                )

                counter += 1

            used_names.add(download_name.lower())

            processed_results.append({

                "original_name": uploaded_file.name,

                "download_name": download_name,

                "content": corrected_content,

                "stats": stats,

                "corrections": corrections,

                "errors": errors

            })

            progress_bar.progress(
                (index + 1) / total_files
            )

        status_text.empty()
        progress_bar.empty()

        st.session_state.results = processed_results


# ============================================================
# DISPLAY RESULTS
# ============================================================

results = st.session_state.results

if results is not None:

    st.divider()

    if not results:

        st.error("No files could be processed successfully.")

    else:

        # Summary statistics.
        total_files = len(results)

        total_corrections = sum(
            r["stats"]["spelling_corrections"]
            for r in results
        )

        total_errors = sum(
            r["stats"]["error_count"]
            for r in results
        )

        total_lines = sum(
            r["stats"]["checked_lines"]
            for r in results
        )

        st.subheader("📊 Processing Summary")

        col1, col2, col3, col4 = st.columns(4)

        col1.metric("Files processed", total_files)

        col2.metric("Lines checked", total_lines)

        col3.metric("Spelling corrections", total_corrections)

        col4.metric("Remaining errors", total_errors)

        # Individual file results.
        st.subheader("📁 Individual File Results")

        for result in results:

            stats = result["stats"]

            status = (
                "✅ No remaining errors"
                if stats["error_count"] == 0
                else "⚠️ Errors found"
            )

            with st.expander(
                f"{status} — {result['original_name']}"
            ):

                st.write(
                    f"**Output file:** {result['download_name']}"
                )

                st.write(
                    f"Lines checked: {stats['checked_lines']}"
                )

                st.write(
                    f"Uppercase corrections: "
                    f"{stats['uppercase_corrections']}"
                )

                st.write(
                    f"Formatting corrections: "
                    f"{stats['formatting_corrections']}"
                )

                st.write(
                    f"Spelling corrections: "
                    f"{stats['spelling_corrections']}"
                )

                st.write(
                    f"Blank lines removed: "
                    f"{stats['removed_blank_lines']}"
                )

                st.write(
                    f"Insufficient-data lines removed: "
                    f"{stats['removed_insufficient_lines']}"
                )

                st.write(
                    f"Punctuation corrections: "
                    f"{stats['punctuation_corrections']}"
                )

                if not stats["marker_found"]:

                    st.warning(
                        "Stop marker was not found. "
                        "The entire file was checked."
                    )

                if result["corrections"]:

                    st.markdown("**Spelling corrections:**")

                    for correction in result["corrections"]:

                        st.write(
                            f"- Line {correction['line']}: "
                            f"`{correction['old']}` → "
                            f"`{correction['new']}`"
                        )

                if result["errors"]:

                    st.markdown("**Unresolved errors:**")

                    for error in result["errors"]:

                        st.error(
                            f"Line {error['line']}: "
                            f"{error['message']}"
                        )

                else:

                    st.success(
                        "No validation errors found."
                    )

        # Download corrected files.
        st.divider()

        st.subheader("📥 Download Results")

        download_data, download_name, mime_type = (
            create_download(results)
        )

        st.download_button(
            label=(
                "⬇️ Download Corrected TXT File"
                if len(results) == 1
                else "⬇️ Download All Corrected Files (ZIP)"
            ),
            data=download_data,
            file_name=download_name,
            mime=mime_type,
            type="primary",
            use_container_width=True
        )

        # Download processing report.
        report = generate_report(results)

        st.download_button(
            label="📄 Download Processing Report",
            data=report.encode("utf-8"),
            file_name="processing_report.txt",
            mime="text/plain",
            use_container_width=True
        )

        with st.expander("View complete processing report"):

            st.text_area(
                "Processing Report",
                report,
                height=350,
                disabled=True
            )