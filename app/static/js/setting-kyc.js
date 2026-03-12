// Map each ID type to its required upload slots
const ID_TYPE_SLOTS = {
    passport: [
        { key: "passport", label: "Passport (photo page)" }
    ],
    national_id: [
        { key: "national_id_front", label: "National ID — Front" },
        { key: "national_id_back",  label: "National ID — Back" }
    ],
    drivers_license: [
        { key: "drivers_license_front", label: "Driver's License — Front" },
        { key: "drivers_license_back",  label: "Driver's License — Back" }
    ],
    voters_card: [
        { key: "voters_card", label: "Voter's Card" }
    ]
};

let selectedIdType = null;
let uploadedFiles = {};  // { document_type: File }

// ── ID Type Selection ────────────────────────────────────────────
document.querySelectorAll(".id-type-card").forEach(card => {
    card.addEventListener("click", () => {
        document.querySelectorAll(".id-type-card").forEach(c => c.classList.remove("active"));
        card.classList.add("active");
        selectedIdType = card.dataset.type;
        renderUploadSlots(selectedIdType);
    });
});

function renderUploadSlots(idType) {
    const slots = ID_TYPE_SLOTS[idType];
    const container = document.getElementById("uploadSlots");
    container.innerHTML = "";

    slots.forEach(slot => {
        container.innerHTML += `
            <div class="upload-slot" id="slot-${slot.key}">
                <div class="upload-slot-header">
                    <span class="slot-label">${slot.label}</span>
                    <span class="slot-status" id="status-${slot.key}"></span>
                </div>
                <div class="file-drop-zone" id="drop-${slot.key}">
                    <input type="file" id="file-${slot.key}"
                           accept="image/jpeg,image/png,image/webp,application/pdf"
                           style="display:none;"
                           onchange="handleFileSelect(this, '${slot.key}')">
                    <div class="drop-zone-content" onclick="document.getElementById('file-${slot.key}').click()">
                        <i class="fas fa-cloud-upload-alt"></i>
                        <p>Click to upload or drag & drop</p>
                        <span>JPG, PNG, WEBP or PDF — max 10MB</span>
                    </div>
                    <div class="file-preview" id="preview-${slot.key}" style="display:none;"></div>
                </div>
            </div>
        `;
    });

    document.getElementById("uploadSection").style.display = "block";
    document.getElementById("addressSection").style.display = "block";
    document.getElementById("submitSection").style.display = "block";

    // Re-attach proof of address listener
    document.getElementById("file-proof_of_address").onchange = function() {
        handleFileSelect(this, "proof_of_address");
    };

    updateChecklist();
}

// ── File Selection ───────────────────────────────────────────────
function handleFileSelect(input, documentType) {
    const file = input.files[0];
    if (!file) return;

    const maxSize = 10 * 1024 * 1024;
    const allowedTypes = ["image/jpeg", "image/png", "image/webp", "application/pdf"];

    if (!allowedTypes.includes(file.type)) {
        showToast("Only JPG, PNG, WEBP, or PDF files are allowed.", "error");
        input.value = "";
        return;
    }

    if (file.size > maxSize) {
        showToast("File size must be under 10MB.", "error");
        input.value = "";
        return;
    }

    uploadedFiles[documentType] = file;
    showFilePreview(documentType, file);
    updateChecklist();
}

function showFilePreview(documentType, file) {
    const preview = document.getElementById(`preview-${documentType}`);
    const dropContent = document.querySelector(`#drop-${documentType} .drop-zone-content`);
    const statusEl = document.getElementById(`status-${documentType}`);

    preview.style.display = "flex";
    dropContent.style.display = "none";

    const isImage = file.type.startsWith("image/");
    preview.innerHTML = isImage
        ? `<img src="${URL.createObjectURL(file)}" alt="preview">
           <button type="button" onclick="clearFile('${documentType}')"><i class="fas fa-times"></i></button>`
        : `<i class="fas fa-file-pdf"></i>
           <span>${file.name}</span>
           <button type="button" onclick="clearFile('${documentType}')"><i class="fas fa-times"></i></button>`;

    statusEl.innerHTML = `<i class="fas fa-check-circle text-success"></i> Ready`;
}

function clearFile(documentType) {
    delete uploadedFiles[documentType];
    const preview = document.getElementById(`preview-${documentType}`);
    const dropContent = document.querySelector(`#drop-${documentType} .drop-zone-content`);
    const statusEl = document.getElementById(`status-${documentType}`);
    const input = document.getElementById(`file-${documentType}`);

    preview.style.display = "none";
    dropContent.style.display = "flex";
    statusEl.innerHTML = "";
    input.value = "";
    updateChecklist();
}

// ── Checklist & Submit Button ────────────────────────────────────
function updateChecklist() {
    if (!selectedIdType) return;

    const required = [
        ...ID_TYPE_SLOTS[selectedIdType].map(s => s.key),
        "proof_of_address"
    ];

    const checklist = document.getElementById("kycChecklist");
    const submitBtn = document.getElementById("submitKycBtn");

    checklist.innerHTML = required.map(key => {
        const uploaded = !!uploadedFiles[key];
        const label = key.replace(/_/g, " ").replace(/\b\w/g, l => l.toUpperCase());
        return `<div class="checklist-item ${uploaded ? 'done' : ''}">
            <i class="fas fa-${uploaded ? 'check-circle' : 'circle'}"></i>
            <span>${label}</span>
        </div>`;
    }).join("");

    const allUploaded = required.every(key => !!uploadedFiles[key]);
    submitBtn.disabled = !allUploaded;
}

// ── Submission ───────────────────────────────────────────────────
document.getElementById("submitKycBtn")?.addEventListener("click", async () => {
    const btn = document.getElementById("submitKycBtn");
    btn.disabled = true;
    btn.innerHTML = `<i class="fas fa-spinner fa-spin"></i> Uploading...`;

    const results = [];

    for (const [documentType, file] of Object.entries(uploadedFiles)) {
        const formData = new FormData();
        formData.append("document_type", documentType);
        formData.append("file", file);

        try {
            const response = await fetch("/kyc/upload", {
                method: "POST",
                body: formData,
            });
            const data = await response.json();
            results.push({ documentType, success: response.ok, message: data.message || data.error });
        } catch (err) {
            results.push({ documentType, success: false, message: "Network error." });
        }
    }

    const allSucceeded = results.every(r => r.success);

    if (allSucceeded) {
        showToast("Documents submitted successfully! We'll review them within 1–2 business days.", "success");
        setTimeout(() => location.reload(), 2000);
    } else {
        const failed = results.filter(r => !r.success).map(r => r.message).join(", ");
        showToast(`Some uploads failed: ${failed}`, "error");
        btn.disabled = false;
        btn.innerHTML = `<i class="fas fa-paper-plane"></i> Submit for Verification`;
    }
});

// ── Remove Submitted Document ────────────────────────────────────
async function removeDocument(docId) {
    if (!confirm("Remove this document? You will need to resubmit it.")) return;

    try {
        const response = await fetch(`/kyc/document/${docId}/remove`, { method: "DELETE" });
        const data = await response.json();

        if (response.ok) {
            showToast(data.message, "success");
            setTimeout(() => location.reload(), 1000);
        } else {
            showToast(data.error, "error");
        }
    } catch {
        showToast("Failed to remove document.", "error");
    }
}

// ── Toast Helper ─────────────────────────────────────────────────
function showToast(message, type = "info") {
    // Hook into your existing toast system if you have one
    // Otherwise a simple fallback:
    const toast = document.createElement("div");
    toast.className = `toast-alert toast-${type}`;
    toast.textContent = message;
    document.body.appendChild(toast);
    setTimeout(() => toast.remove(), 4000);
}