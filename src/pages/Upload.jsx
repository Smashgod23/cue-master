import { useState, useRef, useCallback, useEffect } from "react";
import { useNavigate, Link } from "react-router-dom";

const ACCEPTED_TYPES = {
  "application/pdf": ".pdf",
  "text/plain": ".txt",
  "image/png": ".png",
  "image/jpeg": ".jpg",
};

const ACCEPTED_EXTENSIONS = Object.values(ACCEPTED_TYPES).join(", ");

// Treat touch-primary devices (no hover, coarse pointer) as mobile so we can
// surface an explicit "Take Photo" affordance. Desktops keep drag-and-drop.
const MOBILE_QUERY = "(hover: none) and (pointer: coarse)";

export default function Upload() {
  const navigate = useNavigate();
  const fileInputRef = useRef(null);
  const cameraInputRef = useRef(null);
  const [dragging, setDragging] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [error, setError] = useState(null);
  const [fileName, setFileName] = useState(null);
  const [isMobile, setIsMobile] = useState(() => {
    if (typeof window === "undefined" || !window.matchMedia) return false;
    return window.matchMedia(MOBILE_QUERY).matches;
  });

  useEffect(() => {
    if (typeof window === "undefined" || !window.matchMedia) return;
    const mql = window.matchMedia(MOBILE_QUERY);
    const onChange = (e) => setIsMobile(e.matches);
    mql.addEventListener?.("change", onChange);
    return () => mql.removeEventListener?.("change", onChange);
  }, []);

  const handleFile = useCallback(async (file) => {
    if (!file) return;

    // Validate file type
    const ext = file.name.split(".").pop().toLowerCase();
    const validExts = ["pdf", "txt", "png", "jpg", "jpeg"];
    if (!validExts.includes(ext)) {
      setError(`Unsupported file type (.${ext}). Please use ${ACCEPTED_EXTENSIONS}.`);
      return;
    }

    // 50 MB limit
    const MAX_BYTES = 50 * 1024 * 1024;
    if (file.size > MAX_BYTES) {
      setError("File is too large (max 50 MB). Try splitting the script into smaller sections.");
      return;
    }

    setError(null);
    setFileName(file.name);
    setUploading(true);

    try {
      const formData = new FormData();
      formData.append("file", file);

      // 5-minute timeout — large scanned PDFs can take a while with OCR
      const controller = new AbortController();
      const timeoutId = setTimeout(() => controller.abort(), 5 * 60 * 1000);
      let res;
      try {
        res = await fetch("/api/upload", { method: "POST", body: formData, signal: controller.signal });
      } finally {
        clearTimeout(timeoutId);
      }

      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body.detail || `Upload failed (${res.status})`);
      }

      // Clear any stale data from a previous upload before storing new script
      sessionStorage.removeItem("parsedScript");
      sessionStorage.removeItem("rehearsalSetup");
      const parsed = await res.json();
      sessionStorage.setItem("parsedScript", JSON.stringify(parsed));
      navigate("/review");
    } catch (err) {
      if (err.name === "AbortError") {
        setError("Upload timed out. The file may be too large or complex to parse. Try a smaller file or a plain text version.");
      } else {
        setError(err.message);
      }
      setUploading(false);
    }
  }, [navigate]);

  const onDrop = useCallback((e) => {
    e.preventDefault();
    setDragging(false);
    const file = e.dataTransfer?.files?.[0];
    handleFile(file);
  }, [handleFile]);

  const onDragOver = useCallback((e) => {
    e.preventDefault();
    setDragging(true);
  }, []);

  const onDragLeave = useCallback(() => setDragging(false), []);

  return (
    <div className="min-h-screen bg-parchment flex flex-col">
      {/* Header */}
      <header className="px-6 py-4 border-b border-parchment-deep bg-parchment-warm/60">
        <div className="max-w-4xl mx-auto flex items-center gap-3">
          <Link to="/" className="flex items-center gap-3 no-underline">
            <div className="w-9 h-9 rounded-lg bg-crimson flex items-center justify-center shadow-sm shadow-crimson/20">
              <svg className="w-5 h-5 text-white" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <path d="M12 2C6.48 2 2 6 2 10.5c0 2.5 1.5 5 3.5 6.5L4 22l4-2.5c1.2.5 2.6.5 4 .5 5.52 0 10-4 10-8.5S17.52 2 12 2z" />
              </svg>
            </div>
            <div>
              <h1 className="font-serif text-lg font-bold text-ink leading-tight">Cue Master</h1>
              <span className="font-sans text-[10px] text-warmgray uppercase tracking-[0.15em]">Upload Your Script</span>
            </div>
          </Link>
        </div>
      </header>

      {/* Main */}
      <main className="flex-1 flex flex-col items-center justify-center px-6 py-12">
        <div className="max-w-xl w-full animate-fade-in-up">
          <h2 className="font-serif text-2xl sm:text-3xl font-bold text-ink text-center">
            Bring your script to the stage
          </h2>
          <p className="font-body text-sm text-ink-soft text-center mt-3 max-w-md mx-auto">
            Drop a PDF, text file, or photo of your script pages below.
            We'll parse the dialogue and stage directions automatically.
          </p>

          {/* Upload zone — drag-drop on desktop, explicit camera/library buttons on mobile */}
          {uploading ? (
            <div className="mt-8 border-2 border-dashed border-parchment-deep bg-parchment-warm/30 rounded-2xl p-12 text-center">
              <div className="flex flex-col items-center gap-4">
                <div className="w-12 h-12 rounded-full border-3 border-parchment-deep border-t-crimson animate-spin" />
                <p className="font-serif text-lg text-ink italic">
                  The Dramaturg is reading your script...
                </p>
                <p className="font-sans text-xs text-warmgray">{fileName}</p>
              </div>
            </div>
          ) : isMobile ? (
            <div className="mt-8 rounded-2xl border-2 border-dashed border-parchment-deep bg-parchment-warm/30 p-6">
              <p className="font-serif text-lg text-ink text-center">
                How would you like to add your script?
              </p>
              <div className="mt-5 flex flex-col gap-3">
                <button
                  type="button"
                  onClick={() => cameraInputRef.current?.click()}
                  className="flex items-center justify-center gap-3 rounded-xl bg-crimson text-white font-sans text-sm font-medium py-3.5 px-4 shadow-sm shadow-crimson/20 active:scale-[0.99] transition"
                >
                  <svg className="w-5 h-5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
                    <path d="M23 19a2 2 0 0 1-2 2H3a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h4l2-3h6l2 3h4a2 2 0 0 1 2 2z" />
                    <circle cx="12" cy="13" r="4" />
                  </svg>
                  Take a photo
                </button>
                <button
                  type="button"
                  onClick={() => fileInputRef.current?.click()}
                  className="flex items-center justify-center gap-3 rounded-xl bg-parchment border border-parchment-deep text-ink font-sans text-sm font-medium py-3.5 px-4 active:scale-[0.99] transition"
                >
                  <svg className="w-5 h-5 text-warmgray" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
                    <rect x="3" y="3" width="18" height="18" rx="2" ry="2" />
                    <circle cx="8.5" cy="8.5" r="1.5" />
                    <polyline points="21 15 16 10 5 21" />
                  </svg>
                  Choose from photos or files
                </button>
              </div>
              <p className="font-sans text-[11px] text-warmgray text-center mt-4">
                Accepts {ACCEPTED_EXTENSIONS}
              </p>
            </div>
          ) : (
            <div
              onDrop={onDrop}
              onDragOver={onDragOver}
              onDragLeave={onDragLeave}
              onClick={() => fileInputRef.current?.click()}
              className={`mt-8 border-2 border-dashed rounded-2xl p-12 text-center transition-all duration-200 cursor-pointer
                ${dragging
                  ? "border-gold bg-gold/5 scale-[1.01]"
                  : "border-parchment-deep hover:border-warmgray-light bg-parchment-warm/30"
                }
              `}
            >
              <div className="w-14 h-14 mx-auto rounded-xl bg-parchment-deep/40 flex items-center justify-center mb-4">
                <svg className="w-7 h-7 text-warmgray" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
                  <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" />
                  <polyline points="17 8 12 3 7 8" />
                  <line x1="12" y1="3" x2="12" y2="15" />
                </svg>
              </div>
              <p className="font-serif text-lg text-ink">
                Drag and drop your script here
              </p>
              <p className="font-sans text-xs text-warmgray mt-2">
                or click to choose a file - accepts {ACCEPTED_EXTENSIONS}
              </p>
            </div>
          )}

          {/* General file picker — used by desktop click-to-browse and mobile "Choose from photos or files" */}
          <input
            ref={fileInputRef}
            type="file"
            accept=".pdf,.txt,.png,.jpg,.jpeg"
            className="hidden"
            onChange={(e) => {
              handleFile(e.target.files?.[0]);
              e.target.value = "";
            }}
          />
          {/* Mobile camera input — capture forces the rear camera instead of the photo library */}
          <input
            ref={cameraInputRef}
            type="file"
            accept="image/*"
            capture="environment"
            className="hidden"
            onChange={(e) => {
              handleFile(e.target.files?.[0]);
              e.target.value = "";
            }}
          />

          {/* Error */}
          {error && (
            <div className="mt-4 p-4 rounded-xl bg-crimson/5 border border-crimson/20 text-center animate-fade-in-up">
              <p className="font-body text-sm text-crimson">{error}</p>
            </div>
          )}

          {/* Back link */}
          <div className="mt-8 text-center">
            <Link
              to="/"
              className="font-sans text-xs text-warmgray hover:text-ink-soft transition-colors no-underline"
            >
              &larr; Back to home
            </Link>
          </div>
        </div>
      </main>
    </div>
  );
}
