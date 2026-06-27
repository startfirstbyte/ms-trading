import { useState, useEffect } from 'react'
import styles from './MethodInfo.module.css'

export function MethodInfo() {
  const [open, setOpen] = useState(false)

  useEffect(() => {
    if (!open) return
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') setOpen(false) }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [open])

  return (
    <>
      <button
        className={styles.infoBtn}
        onClick={() => setOpen(true)}
        title="Giới thiệu phương pháp"
        aria-label="Giới thiệu phương pháp"
      >
        <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
          <circle cx="12" cy="12" r="10" />
          <line x1="12" y1="16" x2="12" y2="12" />
          <line x1="12" y1="8" x2="12.01" y2="8" />
        </svg>
      </button>

      {open && (
        <div className={styles.overlay} onClick={() => setOpen(false)}>
          <div className={styles.modal} onClick={e => e.stopPropagation()}>
            <header className={styles.modalHeader}>
              <h2 className={styles.title}>Phương pháp giao dịch theo Market Structure</h2>
              <button className={styles.close} onClick={() => setOpen(false)} aria-label="Đóng">✕</button>
            </header>

            <div className={styles.body}>
              <p className={styles.lead}>
                Hệ thống đọc <strong>cấu trúc thị trường</strong> tự động từ dữ liệu giá realtime,
                phát hiện các điểm phá vỡ cấu trúc và để AI đề xuất kịch bản giao dịch.
              </p>

              <h3 className={styles.h3}>Quy trình 4 bước</h3>
              <ol className={styles.steps}>
                <li>
                  <span className={styles.stepTag}>ZigZag</span>
                  Lọc nhiễu, xác định các đỉnh/đáy quan trọng (swing high / swing low) của giá.
                </li>
                <li>
                  <span className={styles.stepTag}>Market Structure</span>
                  Nối các swing để xác định xu hướng đang tăng (HH–HL) hay giảm (LH–LL).
                </li>
                <li>
                  <span className={styles.stepTag}>BOS / CHOCH</span>
                  <strong>BOS</strong> (Break of Structure) xác nhận xu hướng tiếp diễn;{' '}
                  <strong>CHOCH</strong> (Change of Character) cảnh báo khả năng đảo chiều.
                </li>
                <li>
                  <span className={styles.stepTag}>AI</span>
                  Mô hình tổng hợp cấu trúc + vùng giá để đưa ra nhận định và quản lý lệnh.
                </li>
              </ol>

              <h3 className={styles.h3}>Cách dùng</h3>
              <ul className={styles.tips}>
                <li>Chọn cặp tiền và khung thời gian trên thanh công cụ.</li>
                <li>Nhấn <strong>↺ MS</strong> để tính lại cấu trúc thị trường cho khung hiện tại.</li>
                <li>Vẽ một <strong>Long/Short Position</strong> rồi khoá lại để AI theo dõi và quản lý lệnh.</li>
              </ul>

              <p className={styles.note}>
                Lưu ý: đây là công cụ hỗ trợ phân tích, không phải lời khuyên đầu tư.
                Luôn tự quản lý rủi ro cho mỗi lệnh.
              </p>
            </div>
          </div>
        </div>
      )}
    </>
  )
}
