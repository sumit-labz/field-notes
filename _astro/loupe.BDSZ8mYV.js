let t=null,s=null;function a(){const e=document.createElement("dialog");return e.className="loupe",e.setAttribute("aria-label","Photograph"),e.innerHTML=`
    <figure class="loupe__sheet">
      <img class="loupe__img" alt="" />
      <figcaption class="loupe__cap">
        <span class="loupe__title"></span>
        <a class="loupe__go" href="#">read the post →</a>
      </figcaption>
    </figure>
    <button type="button" class="loupe__close" aria-label="Put it back">×</button>`,e.addEventListener("click",o=>{(o.target===e||o.target.closest(".loupe__close"))&&u()}),e.addEventListener("close",()=>{document.documentElement.classList.remove("loupe-open"),s?.focus({preventScroll:!0})}),document.body.appendChild(e),e}function u(){if(!t?.open)return;if(matchMedia("(prefers-reduced-motion: reduce)").matches){t.close();return}t.classList.add("is-closing"),setTimeout(()=>{t?.classList.remove("is-closing"),t?.close()},220)}function c(e){t??=a();const o=t.querySelector(".loupe__img");o.src=e.dataset.loupe??"",e.dataset.loupeW&&e.dataset.loupeH&&(o.width=Number(e.dataset.loupeW),o.height=Number(e.dataset.loupeH)),t.querySelector(".loupe__title").textContent=e.dataset.loupeTitle??"";const l=t.querySelector(".loupe__go");l.href=e.dataset.loupeHref??"#",l.hidden=!e.dataset.loupeHref,s=e,document.documentElement.classList.add("loupe-open"),t.showModal()}document.addEventListener("click",e=>{const o=e.target.closest("[data-loupe]");o&&(e.preventDefault(),e.stopPropagation(),c(o))});
