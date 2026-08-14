// 관리자 전용 진입점. "키워드 관리"라는 기능성 이름 대신 목적지 화면 제목과 똑같이
// "관리자 화면"이라고 불러서, 이게 일반 사용자용 기능이 아니라 관리자를 위한
// 버튼이라는 걸 이름만 보고도 확실히 알 수 있게 한다. 항상 새 탭으로 연다 —
// 통계/출처 분해까지 보는 전용 페이지라 지금 화면 위에 덮어씌우면 좁게만 보인다.
export default function AdminLink({ className }) {
  return (
    <a href="/admin" target="_blank" rel="noopener noreferrer" className={className}>
      관리자 화면
    </a>
  )
}
