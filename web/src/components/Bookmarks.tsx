import { colr, lvl, type BookmarkItem } from "../types";

interface Props {
  bookmarks: BookmarkItem[];
  onOpenReport: (b: BookmarkItem) => void;
  onRemoveBookmark: (index: number) => void;
}

export function Bookmarks({ bookmarks, onOpenReport, onRemoveBookmark }: Props) {
  return (
    <div className="view" id="v-bookmarks">
      <section className="card">
        <h3>
          Bookmarks{" "}
          <small style={{ color: "var(--mut)", fontWeight: 400 }}>
            · {bookmarks.length} saved
          </small>
        </h3>
        {bookmarks.length > 0 ? (
          <div className="bmg">
            {bookmarks.map((b, i) => {
              const l = lvl(b.sc);
              return (
                <div className="bmc" key={i}>
                  <div className="tok">
                    <h2 style={{ fontSize: "17px" }}>
                      {b.n} <span>({b.s})</span>
                    </h2>
                  </div>
                  <div
                    style={{
                      display: "flex",
                      alignItems: "center",
                      gap: "10px",
                      margin: "10px 0",
                    }}
                  >
                    <span className={`badge ${l}`}>{l}</span>
                    <b style={{ color: colr(l) }}>{b.sc}/100</b>
                  </div>
                  <div className="trk">
                    <i style={{ width: `${b.sc}%`, background: colr(l) }} />
                  </div>
                  <div style={{ display: "flex", gap: "8px", marginTop: "14px" }}>
                    <button
                      className="btn sm2"
                      data-bo={i}
                      onClick={() => onOpenReport(b)}
                    >
                      Open report
                    </button>
                    <button
                      className="ghost sm"
                      data-br={i}
                      onClick={() => onRemoveBookmark(i)}
                    >
                      Remove
                    </button>
                  </div>
                </div>
              );
            })}
          </div>
        ) : (
          <p style={{ color: "var(--mut)" }}>
            No bookmarks yet. Use ☆ Save on a report or in Scan History.
          </p>
        )}
      </section>
    </div>
  );
}
