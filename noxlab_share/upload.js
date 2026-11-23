(() => {
    const form = document.getElementById("upload-form");
    const picker = document.getElementById("files");
    const button = document.getElementById("upload-button");
    const progress = document.getElementById("upload-progress");
    const state = document.getElementById("upload-state");
    const selection = document.getElementById("selection-list");
    const limit = Number(form.dataset.limit);
    let nextFile = 0;
    let transfer = null;
    let busy = false;
    let savedNames = [];

    async function post(path, headers, body) {
        const controller = new AbortController();
        const timer = setTimeout(() => controller.abort(), 90000);
        try {
            const response = await fetch("/upload/" + path, {
                method: "POST", headers, body, signal: controller.signal
            });
            const result = await response.json();
            if (!response.ok) {
                const error = new Error(result.error || "The PC rejected the upload.");
                error.status = response.status;
                throw error;
            }
            return result;
        } finally {
            clearTimeout(timer);
        }
    }

    function readSlice(blob) {
        return new Promise((resolve, reject) => {
            const reader = new FileReader();
            reader.onload = () => resolve(reader.result);
            reader.onerror = () => reject(new Error(
                "The browser could not read this file. Save a local copy and choose it through Files."
            ));
            reader.readAsArrayBuffer(blob);
        });
    }

    async function sendFile(file, index, count) {
        const label = (index + 1) + "/" + count + " " + file.name;
        state.textContent = "Opening " + label;
        if (!transfer) {
            const password = form.elements.password?.value || "";
            const session = await post("start", {"Content-Type": "application/json"},
                JSON.stringify({name: file.name, size: file.size, password}));
            transfer = {...session, offset: 0};
        }
        do {
            const offset = transfer.offset;
            const part = file.slice(offset, offset + transfer.chunkSize);
            const bytes = await readSlice(part);
            if (bytes.byteLength !== part.size) throw new Error("File changed. Choose it again through Files.");
            const headers = {"Content-Type": "application/octet-stream",
                "X-Upload-Token": transfer.token, "X-Offset": String(offset)};
            let receipt;
            for (let attempt = 1; ; attempt++) {
                try {
                    receipt = await post("chunk", headers, bytes);
                    break;
                } catch (error) {
                    if (error.status || attempt === 3) throw error;
                    state.textContent = "Retrying " + label + " at " + offset.toLocaleString() + " bytes";
                    await new Promise(resolve => setTimeout(resolve, attempt * 500));
                }
            }
            if (receipt.offset !== offset + bytes.byteLength || receipt.saved !== (receipt.offset === file.size)) {
                throw new Error("Unexpected reply from the PC. Check its activity log.");
            }
            transfer.offset = receipt.offset;
            progress.value = file.size ? receipt.offset * 100 / file.size : 100;
            state.textContent = label + " | " + Math.floor(progress.value) + "% received";
            if (receipt.saved) {
                savedNames.push(receipt.name);
                transfer = null;
                return;
            }
        } while (transfer.offset < file.size);
    }

    picker.addEventListener("change", () => {
        selection.replaceChildren();
        for (const file of picker.files) {
            const row = document.createElement("li");
            const size = document.createElement("small");
            size.textContent = file.size < 1024 ? file.size + " B" : file.size < 1048576
                ? Math.ceil(file.size / 1024) + " KiB" : (file.size / 1048576).toFixed(1) + " MiB";
            row.append(document.createTextNode(file.name), size);
            selection.append(row);
        }
        selection.hidden = !picker.files.length;
        if (transfer) post("cancel", {"Content-Type": "application/octet-stream",
            "X-Upload-Token": transfer.token}, new ArrayBuffer(0)).catch(() => {});
        nextFile = 0;
        transfer = null;
        savedNames = [];
        progress.hidden = true;
        state.textContent = "";
        delete state.dataset.kind;
        button.textContent = "Upload to PC";
    });

    form.addEventListener("submit", async event => {
        event.preventDefault();
        if (busy || !form.reportValidity()) return;
        const files = Array.from(picker.files);
        delete state.dataset.kind;
        if (limit && files.reduce((total, file) => total + file.size, 0) > limit) {
            state.dataset.kind = "error";
            state.textContent = "Selection exceeds the PC's upload limit. Choose fewer files or raise the limit on the PC.";
            return;
        }
        busy = button.disabled = picker.disabled = true;
        progress.hidden = false;
        try {
            while (nextFile < files.length) {
                progress.value = transfer ? transfer.offset * 100 / (files[nextFile].size || 1) : 0;
                await sendFile(files[nextFile], nextFile, files.length);
                nextFile++;
            }
            state.textContent = "Saved on PC: " + savedNames.join(", ");
            form.reset();
            selection.replaceChildren();
            selection.hidden = true;
            nextFile = 0;
            savedNames = [];
            button.textContent = "Upload to PC";
        } catch (error) {
            state.dataset.kind = "error";
            if (error.status === 410) transfer = null;
            const networkError = error instanceof TypeError || error.name === "AbortError";
            state.textContent = networkError
                ? "No reply from the PC. " + (transfer?.offset || 0).toLocaleString() +
                    " bytes confirmed for this file. Keep this page open and tap Retry."
                : error.message;
            button.textContent = "Retry upload";
        } finally {
            busy = button.disabled = picker.disabled = false;
        }
    });
})();
