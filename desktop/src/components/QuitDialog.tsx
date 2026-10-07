interface QuitDialogProps {
  onChoice: (choice: "keep" | "stop" | "cancel") => void;
}

export default function QuitDialog({ onChoice }: QuitDialogProps) {
  return (
    <div className="modal-backdrop" role="dialog" aria-label="Active monitoring">
      <div className="modal">
        <div className="modal-header">
          <span className="modal-icon">⚠️</span>
          <h2 className="modal-title">Active Monitoring Session</h2>
        </div>
        <p className="modal-body">
          A monitoring run is still active. What would you like to do before closing NAS Air Intelligence?
        </p>
        <div className="modal-actions">
          <button
            type="button"
            className="btn btn-primary"
            onClick={() => onChoice("keep")}
          >
            Keep monitoring in background
          </button>
          <button
            type="button"
            className="btn btn-danger"
            onClick={() => onChoice("stop")}
          >
            Stop monitoring and quit
          </button>
          <button
            type="button"
            className="btn btn-secondary"
            onClick={() => onChoice("cancel")}
          >
            Cancel
          </button>
        </div>
      </div>
    </div>
  );
}
