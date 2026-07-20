import { LoaderCircle } from 'lucide-react'

interface PageLoadingSkeletonProps {
  label: string
  stage?: string
}

/** 页面加载骨架屏，替代纯 spinner 等待态 */
export function PageLoadingSkeleton({ label, stage }: PageLoadingSkeletonProps) {
  return (
    <div aria-busy="true" aria-live="polite" className="page page-loading-skeleton">
      <div className="page-loading-skeleton__hero">
        <span className="skeleton skeleton--eyebrow" />
        <span className="skeleton skeleton--title" />
        <span className="skeleton skeleton--line skeleton--line-wide" />
        <span className="skeleton skeleton--line" />
      </div>
      <div className="page-loading-skeleton__grid">
        {Array.from({ length: 3 }, (_, index) => (
          <div className="page-loading-skeleton__card" key={index}>
            <span className="skeleton skeleton--block" />
            <span className="skeleton skeleton--line" />
            <span className="skeleton skeleton--line skeleton--line-short" />
          </div>
        ))}
      </div>
      <p className="page-loading-skeleton__status" role="status">
        <LoaderCircle className="spin" size={16} />
        {label}{stage ? ` · ${stage}` : ''}
      </p>
    </div>
  )
}
