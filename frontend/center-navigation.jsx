import React from 'react';
import {flushSync} from 'react-dom';
import {createRoot} from 'react-dom/client';
import {CenterArtwork} from './center-artwork.jsx';

function CollectionCard({card}) {
  const {name, path, count, directory, select, preview} = card;
  return <button type="button" className={'collection' + (directory ? ' directory-card' : '')}
    aria-label={path ? name + ' · ' + path + ' · ' + count : undefined} onClick={select}>
    {preview && <div className="collection-art" ref={preview.attachCover}>
      <CenterArtwork artwork={preview.artwork} title={preview.title} photo={preview.photo} />
    </div>}
    <strong>{name}</strong>
    <span className="muted">{count}</span>
    {path && <div className="muted album-path" title={path}>{path}</div>}
  </button>;
}

function CountCard({card}) {
  return <button type="button" className="count" aria-label={'查看' + card.label} onClick={card.select}>
    <span className="muted">{card.label}</span>
    <strong>{card.count}</strong>
    {card.description && <small className="muted">{card.description}</small>}
    <span className="count-link">查看{card.label} →</span>
  </button>;
}

export function CenterNavigationCards({cards, type}) {
  if (type === 'count') return cards.map(card => <CountCard key={card.id} card={card} />);
  if (type === 'collection') return cards.map(card => <CollectionCard key={card.id} card={card} />);
  return cards.map(card => <button type="button" key={card.id} className={type === 'tag' ? 'chip' : undefined}
    title={card.path || undefined} onClick={card.select}>{card.label}</button>);
}

export function mountCenterNavigation(container) {
  const root = createRoot(container);
  return {
    render(props) { flushSync(() => root.render(<CenterNavigationCards {...props} />)); },
    dispose() { root.unmount(); },
  };
}
