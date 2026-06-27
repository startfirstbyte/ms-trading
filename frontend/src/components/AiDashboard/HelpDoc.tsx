import type { ReactNode } from 'react'
import s from './HelpDoc.module.css'

interface Props {
  onClose: () => void
}

function cx(...classes: (string | false | undefined)[]) {
  return classes.filter(Boolean).join(' ')
}

function Section({ n, title, children }: { n: number; title: string; children: ReactNode }) {
  return (
    <>
      <h3 className={s.h2}><span className={s.num}>{n}</span>{title}</h3>
      {children}
    </>
  )
}

export function HelpDoc({ onClose }: Props) {
  return (
    <div className={s.overlay}>
      <div className={s.head}>
        <span className={s.title}>Phương pháp giao dịch — Hướng dẫn</span>
        <button className={s.close} onClick={onClose} title="Đóng">✕</button>
      </div>

      <div className={s.body}>
        <p className={s.lead}>
          Hệ thống phân tích theo <span className={s.term}>Cấu trúc thị trường (Market Structure)</span> cho
          giao dịch ngắn hạn (scalping), trên nhiều khung thời gian (1m → 1H). AI đọc cấu trúc giá rồi đưa ra
          tín hiệu kèm các mức vào/ra. Tài liệu này giải thích ý nghĩa từng thành phần trên thẻ tín hiệu.
        </p>

        <Section n={1} title="Cấu trúc thị trường là gì">
          <p>Hệ thống tìm các đỉnh/đáy (swing) rồi đọc quan hệ giữa chúng:</p>
          <ul>
            <li><span className={s.term}>HH</span> (Higher High) &amp; <span className={s.term}>HL</span> (Higher Low): đỉnh sau cao hơn, đáy sau cao hơn → <span className={s.term}>xu hướng TĂNG</span>.</li>
            <li><span className={s.term}>LH</span> (Lower High) &amp; <span className={s.term}>LL</span> (Lower Low): đỉnh sau thấp hơn, đáy sau thấp hơn → <span className={s.term}>xu hướng GIẢM</span>.</li>
            <li><span className={s.term}>BOS</span> (Break of Structure): giá phá vỡ cấu trúc <span className={s.dim}>theo chiều xu hướng</span> → xác nhận xu hướng tiếp diễn.</li>
            <li><span className={s.term}>CHOCH</span> (Change of Character): giá phá vỡ <span className={s.dim}>ngược chiều</span> → cảnh báo có thể đảo chiều.</li>
            <li><span className={s.term}>Channel (kênh giá)</span>: vùng giá dao động giữa biên trên (kháng cự) và biên dưới (hỗ trợ).</li>
          </ul>
        </Section>

        <Section n={2} title="Tín hiệu & độ tin cậy">
          <ul>
            <li><span className={`${s.tag} ${s.buy}`}>BUY</span> đề xuất canh MUA · <span className={`${s.tag} ${s.sell}`}>SELL</span> đề xuất canh BÁN · <span className={`${s.tag} ${s.wait}`}>WAIT</span> đứng ngoài chờ.</li>
            <li><span className={s.term}>Độ tin cậy (3 chấm)</span>: 1 chấm xám = THẤP · 2 chấm xanh dương = TRUNG BÌNH · 3 chấm xanh lá = CAO. Càng nhiều chấm, tín hiệu càng đáng tin.</li>
            <li><span className={s.term}>Dòng "→ Chờ giá…"</span> là <span className={s.term}>điều kiện kích hoạt</span>: nêu rõ giá cần phá/về mức nào thì tín hiệu mới có hiệu lực.</li>
          </ul>
        </Section>

        <Section n={3} title="Các mức giá trên thẻ">
          <ul>
            <li><span className={`${s.tag} ${s.buy}`}>BUY ZONE</span> vùng canh MUA (hỗ trợ / đáy kênh).</li>
            <li><span className={`${s.tag} ${s.sell}`}>SELL ZONE</span> vùng canh BÁN (kháng cự / đỉnh kênh).</li>
            <li><span className={`${s.tag} ${s.key}`}>★ KEY LEVEL</span> mốc giá <span className={s.term}>quan trọng nhất</span> đang theo dõi (mức BOS / biên kênh / đỉnh-đáy gần). Số nhỏ bên dưới (vd <span className={s.dim}>~24m</span>) là ước tính thời gian giá chạm mức này.</li>
            <li><span className={s.term}>Entry</span> điểm vào lệnh · <span className={s.term} style={{ color: '#22d3ee' }}>Target (TP)</span> mục tiêu chốt lời · <span className={s.term} style={{ color: '#f85149' }}>SL</span> mức cắt lỗ.</li>
          </ul>
        </Section>

        <Section n={4} title="Vì sao thường thấy WAIT">
          <p>Hệ thống <span className={s.term}>ưu tiên an toàn</span>: chỉ ra BUY/SELL khi hội đủ điều kiện. Sẽ <span className={`${s.tag} ${s.wait}`}>WAIT</span> khi:</p>
          <ul>
            <li>Cấu trúc <span className={s.term}>nhiễu</span>, chưa có BOS/CHOCH rõ ràng (quá nhiều pivot).</li>
            <li>Giá <span className={s.term}>chưa về vùng</span> vào lệnh (đang chờ giá chạm zone).</li>
            <li>Stop loss quá hẹp (rủi ro nhiễu) — xem mục 5.</li>
            <li>Ngược với khung lớn, hoặc đà (momentum) đang mạnh ngược chiều.</li>
          </ul>
        </Section>

        <Section n={5} title="Bộ lọc an toàn">
          <ul>
            <li><span className={s.term}>Khung lớn (15m / 1H)</span>: tín hiệu khung nhỏ phải thuận chiều khung lớn; nếu ngược → chỉ cho độ tin cậy THẤP hoặc WAIT.</li>
            <li><span className={s.term}>Momentum</span>: khi đà giảm/tăng quá mạnh, hệ thống không bắt ngược (tránh "bắt dao rơi").</li>
            <li><span className={s.term}>Stop loss ≥ 1.2× ATR14</span>: SL phải đủ xa để tránh bị quét bởi dao động nhiễu; nếu không đặt được SL hợp lệ → WAIT.</li>
          </ul>
        </Section>

        <Section n={6} title="AI quản trị lệnh của bạn">
          <p>Bạn có thể để AI theo dõi lệnh thật của mình:</p>
          <ul>
            <li>Dùng công cụ <span className={s.term}>Long / Short Position</span> của TradingView vẽ vị thế (có sẵn entry, SL, TP).</li>
            <li><span className={s.term}>Khóa (Lock)</span> vị thế đó = báo cho hệ thống bạn đã vào lệnh.</li>
            <li>AI sẽ đánh giá và hiện badge: <span className={`${s.tag} ${s.buy}`}>Giữ lệnh</span> (cấu trúc còn ủng hộ) · <span className={`${s.tag} ${s.warn}`}>Chốt 1 phần</span> (gần TP, cấu trúc yếu dần) · <span className={`${s.tag} ${s.sell}`}>Đóng lệnh</span> (cấu trúc đảo chiều, bảo vệ vốn).</li>
            <li>Khi giá <span className={s.term}>chạm TP hoặc SL</span>, lệnh tự coi là đã đóng → AI ngừng theo dõi. Bỏ khóa hoặc xóa vị thế cũng dừng theo dõi.</li>
          </ul>
        </Section>

        <Section n={7} title="Cách đọc một thẻ tín hiệu">
          <ul>
            <li>1. Xem <span className={s.term}>khung thời gian</span> và <span className={s.term}>tín hiệu + độ tin cậy</span>.</li>
            <li>2. Đọc <span className={s.term}>điều kiện kích hoạt</span> ("→ Chờ giá…") để biết khi nào tín hiệu có hiệu lực.</li>
            <li>3. Đối chiếu các <span className={s.term}>mức giá</span> (zone / key / entry / TP / SL) với giá hiện tại.</li>
            <li>4. Đọc <span className={s.term}>ghi chú phân tích</span> (dòng dưới cùng) để hiểu lý do.</li>
            <li>5. Ưu tiên các tín hiệu được <span className={s.term}>nhiều khung đồng thuận</span>.</li>
          </ul>
        </Section>

        <Section n={8} title="Lưu ý quan trọng">
          <ul>
            <li>Cuối tuần: Vàng (XAU) và Dầu (USOIL) đóng cửa, chỉ <span className={s.term}>BTC chạy 24/7</span>.</li>
            <li>Tín hiệu độ tin cậy THẤP chỉ mang tính tham khảo.</li>
            <li>Luôn tự quản trị rủi ro — không vào lệnh vượt khả năng chịu lỗ.</li>
          </ul>
        </Section>

        <Section n={9} title="Các đường & nhãn vẽ trên chart">
          <p>Bộ Market Structure vẽ các đường sau — <span className={s.term}>màu = ý nghĩa</span>:</p>
          <div className={s.legend}>
            <div className={s.legendRow}>
              <span className={s.swatch} style={{ borderTopColor: '#26a69a' }} />
              <span><span className={s.term}>Kênh TĂNG</span> — 3 đường song song dốc lên (trên = kháng cự, dưới = hỗ trợ, giữa = trung tuyến).</span>
            </div>
            <div className={s.legendRow}>
              <span className={s.swatch} style={{ borderTopColor: '#ef5350' }} />
              <span><span className={s.term}>Kênh GIẢM</span> — kênh dốc xuống.</span>
            </div>
            <div className={s.legendRow}>
              <span className={s.swatch} style={{ borderTopColor: '#787b86' }} />
              <span><span className={s.term}>Kênh ĐI NGANG</span> — sideway / range.</span>
            </div>
            <div className={s.legendRow}>
              <span className={cx(s.swatch, s.swatchZig)} style={{ borderTopColor: '#26a69a' }} />
              <span><span className={s.term}>Đường nối swing</span> (zíc-zắc nối đỉnh–đáy) — màu theo xu hướng: xanh tăng / đỏ giảm / xám ngang.</span>
            </div>
            <div className={s.legendRow}>
              <span className={s.swatch} style={{ borderTopColor: '#f59e0b' }} />
              <span><span className={s.term} style={{ color: '#f59e0b' }}>Đường BOS/CHOCH (cam)</span> — mức then chốt: phá vỡ → xác nhận tiếp diễn (BOS) hoặc đảo chiều (CHOCH).</span>
            </div>
            <div className={s.legendRow}>
              <span className={cx(s.swatch, s.swatchDash)} style={{ borderTopColor: '#f59e0b' }} />
              <span><span className={s.term}>Đường ngang BOS level</span> (cam, nét đứt, có nhãn giá) — mốc giá đang theo dõi.</span>
            </div>
          </div>

          <p>Nhãn đỉnh–đáy trên chart:</p>
          <ul>
            <li><b style={{ color: '#ef5350' }}>HH / LH</b> — đỉnh (Higher High / Lower High), màu đỏ.</li>
            <li><b style={{ color: '#26a69a' }}>HL / LL</b> — đáy (Higher Low / Lower Low), màu xanh.</li>
            <li><b style={{ color: '#f59e0b' }}>BOS / CHOCH</b> — điểm phá cấu trúc, màu cam đậm.</li>
          </ul>
          <p className={s.dim}>Đường <span className={s.term}>mới nhất</span> vẽ nét đứt + đậm hơn; các bản cũ mờ/mảnh hơn để thấy kênh đã dịch chuyển ra sao.</p>
        </Section>

        <p className={s.note}>
          ⚠️ Đây là công cụ <span className={s.term}>hỗ trợ phân tích</span>, KHÔNG phải lời khuyên đầu tư.
          Mọi quyết định giao dịch và rủi ro là của bạn.
        </p>
      </div>
    </div>
  )
}
